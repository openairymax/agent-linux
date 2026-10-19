#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
airy_rollback.py — AirymaxOS 版本级回滚编排器

按 01-deployment.md §11 的三层模型执行回滚：
  L1  dnf 事务回滚（单个包/事务，秒级）
  L2  unit 状态回滚（daemon-reload + 重启 12 daemons，秒级）
  L3  根分区快照回滚（btrfs set-default / LVM merge，需重启，分钟级）

命令模板、路径与 daemon 名单全部来自同目录 manifest.yaml；本模块只做
清单校验、快照选择、层次决策、前置检查、步骤执行与审计，不复制命令。

权威依据：
  docs/docs-linux/100-operations/01-deployment.md §11
    OS-OPS-021 快照保留、OS-OPS-022 回滚后校验、OS-OPS-023 dnf 配置
    OS-OPS-036 记忆卷独立挂载
  docs/docs-linux/120-development-process/09-release-process.md §7.2-7.5
  docs/docs-linux/100-operations/07-systemd-integration.md §8.1
  docs/docs-linux/50-engineering-standards/10-coding-style/
    scripting_coding_style.md

Usage:
    airy-rollback [apply] [--to VERSION | --snapshot NAME] [options]
    airy-rollback plan  [--to VERSION | --snapshot NAME] [options]
    airy-rollback verify
    airy-rollback prune [--dry-run]
    airy-rollback check

Exit codes:
    0 — 成功
    1 — 步骤执行失败（无步骤生效）
    2 — 用法或清单错误
    3 — 前置检查未通过
    4 — 目标版本/快照不存在
    5 — 快照元数据损坏
    6 — 回滚后校验未通过
    7 — 另一个回滚正在进行（锁冲突）
    8 — 部分成功：已有步骤生效，需人工复核
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

from rollback_types import (
    EXIT_BUSY,
    EXIT_FAIL,
    EXIT_INTEGRITY,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_PREFLIGHT,
    EXIT_TARGET,
    EXIT_USAGE,
    EXIT_VERIFY,
    Check,
    Kind,
    Layer,
    Manifest,
    Outcome,
    Plan,
    Proc,
    RollbackError,
    Runner,
    Snapshot,
    Step,
)

__all__ = [
    "Lock",
    "Subproc",
    "build_parser",
    "build_prune",
    "build_steps",
    "check_manifest",
    "do_apply",
    "do_plan",
    "do_prune",
    "do_verify",
    "expand",
    "expired",
    "load_manifest",
    "main",
    "make_plan",
    "mem_check",
    "pick_layer",
    "pick_snap",
    "preflight",
    "read_release",
    "read_snap",
    "run_steps",
    "scan_snaps",
    "show_checks",
    "show_plan",
    "verify_units",
    "write_audit",
]

DEFAULT_MANIFEST = Path(__file__).resolve().with_name("manifest.yaml")

META_NAME = "rollback.json"
SNAP_PREFIX = "pre-rollback-"
DAEMON_COUNT = 12
TIME_RE = re.compile(r"\{([a-z_]+)\}")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

REQUIRED_CMDS = frozenset({
    "daemon_active",
    "l1_rollback",
    "l2_reload",
    "l2_restart",
    "l3_btrfs_snap",
    "l3_btrfs_default",
    "l3_btrfs_del",
    "l3_lvm_merge",
    "l3_lvm_del",
    "mount_source",
    "reboot",
})

CMD_ARGS = {
    "daemon_active": frozenset({"unit"}),
    "l1_rollback": frozenset({"tx"}),
    "l2_restart": frozenset({"units"}),
    "l3_btrfs_snap": frozenset({"dest"}),
    "l3_btrfs_default": frozenset({"source"}),
    "l3_btrfs_del": frozenset({"dest"}),
    "l3_lvm_merge": frozenset({"source"}),
    "l3_lvm_del": frozenset({"source"}),
    "mount_source": frozenset({"path"}),
}

PLACEHOLDERS = frozenset(
    {arg for args in CMD_ARGS.values() for arg in args} | {"units"}
)


class Subproc:
    """基于 subprocess 的生产执行器。

    执行器自身故障不抛异常：超时返回 124，命令不可用返回 127，使编排
    层对所有失败使用同一路径处理并完整写入审计。
    """

    def run(self, argv: Sequence[str], timeout_s: float) -> Proc:
        """执行一条命令。"""
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return Proc(124, "", f"命令超时: {' '.join(argv)}")
        except OSError as exc:
            return Proc(127, "", f"命令不可用: {exc}")
        return Proc(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


class Lock:
    """回滚互斥锁：同一时刻只允许一个回滚编排。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> "Lock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RollbackError(
                EXIT_BUSY, f"另一个回滚正在进行: {self._path}"
            ) from exc
        self._fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def _fail(key: str, code: int) -> RollbackError:
    return RollbackError(code, f"字段 {key} 非法")


def _text(raw: Mapping[str, Any], key: str, code: int = EXIT_USAGE) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(key, code)
    return value.strip()


def _path(raw: Mapping[str, Any], key: str, code: int = EXIT_USAGE) -> Path:
    return Path(_text(raw, key, code))


def _int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _fail(key, EXIT_USAGE)
    return value


def _float(raw: Mapping[str, Any], key: str) -> float:
    value = raw.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise _fail(key, EXIT_USAGE)
    return float(value)


def _units(
    raw: Mapping[str, Any], code: int = EXIT_USAGE
) -> tuple[str, ...]:
    value = raw.get("daemons")
    if not isinstance(value, list) or not value:
        raise _fail("daemons", code)
    if not all(isinstance(item, str) and item for item in value):
        raise _fail("daemons", code)
    return tuple(value)


def _cmds(raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    block = raw.get("commands")
    if not isinstance(block, Mapping) or not block:
        raise _fail("commands", EXIT_USAGE)
    out: dict[str, tuple[str, ...]] = {}
    for name, argv in block.items():
        if not isinstance(name, str) or not name:
            raise _fail("commands", EXIT_USAGE)
        if not isinstance(argv, list) or not argv:
            raise _fail(name, EXIT_USAGE)
        if not all(isinstance(item, str) and item for item in argv):
            raise _fail(name, EXIT_USAGE)
        out[name] = tuple(argv)
    return out


def _yaml(path: Path) -> Mapping[str, Any]:
    if yaml is None:
        raise RollbackError(EXIT_USAGE, "PyYAML 不可用，请安装 pyyaml")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RollbackError(EXIT_USAGE, f"清单不可读: {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RollbackError(EXIT_USAGE, f"清单必须是映射: {path}")
    return raw


def load_manifest(path: Path) -> Manifest:
    """读取并校验清单结构，返回运行时视图。"""
    raw = _yaml(path)
    return Manifest(
        version=_text(raw, "version"),
        release_file=_path(raw, "release_file"),
        snapshot_root=_path(raw, "snapshot_root"),
        audit_log=_path(raw, "audit_log"),
        pending_flag=_path(raw, "pending_flag"),
        lock_file=_path(raw, "lock_file"),
        memory_path=_path(raw, "memory_path"),
        daemons=_units(raw),
        commands=_cmds(raw),
        retention_days=_int(raw, "retention_days"),
        timeout_s=_float(raw, "timeout_s"),
    )


def _tokens(argv: Sequence[str]) -> set[str]:
    return {match for item in argv for match in TIME_RE.findall(item)}


def _cmd_check(cmds: Mapping[str, tuple[str, ...]]) -> Check:
    missing = sorted(REQUIRED_CMDS - set(cmds))
    if missing:
        return Check("commands", False, "缺少模板: " + ", ".join(missing))
    for name, need in CMD_ARGS.items():
        lack = sorted(need - _tokens(cmds[name]))
        if lack:
            detail = f"{name} 缺少占位符: " + ", ".join(lack)
            return Check("commands", False, detail)
    unknown = set().union(*(_tokens(argv) for argv in cmds.values()))
    extra = sorted(unknown - PLACEHOLDERS)
    if extra:
        return Check("commands", False, "未知占位符: " + ", ".join(extra))
    return Check("commands", True, f"{len(cmds)} 个模板")


def check_manifest(manifest: Manifest) -> tuple[Check, ...]:
    """校验清单语义：路径、daemon 名单与命令模板。"""
    paths = (
        manifest.release_file,
        manifest.snapshot_root,
        manifest.audit_log,
        manifest.pending_flag,
        manifest.lock_file,
        manifest.memory_path,
    )
    units = manifest.daemons
    absolute = all(item.is_absolute() for item in paths)
    return (
        Check(
            "version",
            VERSION_RE.fullmatch(manifest.version) is not None,
            manifest.version,
        ),
        Check(
            "paths",
            absolute,
            "全部为绝对路径" if absolute else "存在相对路径",
        ),
        Check(
            "daemons",
            len(units) == DAEMON_COUNT
            and len(set(units)) == DAEMON_COUNT
            and all(item.endswith(".service") for item in units),
            f"{len(units)} 个 unit",
        ),
        Check(
            "retention",
            manifest.retention_days >= 1,
            f"{manifest.retention_days} 天",
        ),
        Check("timeout", manifest.timeout_s > 0, f"{manifest.timeout_s} 秒"),
        _cmd_check(manifest.commands),
    )


def _snap_ts_text(text: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RollbackError(EXIT_INTEGRITY, f"created 非法: {text}") from exc
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _snap_ts(snap: Snapshot) -> datetime:
    return _snap_ts_text(snap.created)


def read_snap(path: Path) -> Snapshot | None:
    """读取单个快照目录；无元数据返回 None，元数据损坏抛错。"""
    meta = path / META_NAME
    if not meta.is_file():
        return None
    try:
        raw = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RollbackError(EXIT_INTEGRITY, f"{meta}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RollbackError(EXIT_INTEGRITY, f"{meta}: 元数据必须是对象")
    try:
        kind = Kind(_text(raw, "kind", EXIT_INTEGRITY))
    except ValueError as exc:
        raise RollbackError(EXIT_INTEGRITY, f"{meta}: kind 非法") from exc
    created = _text(raw, "created", EXIT_INTEGRITY)
    _snap_ts_text(created)
    tx = raw.get("dnf_tx")
    if tx is not None and not isinstance(tx, int):
        raise RollbackError(EXIT_INTEGRITY, f"{meta}: dnf_tx 必须是整数")
    return Snapshot(
        path=path,
        version=_text(raw, "version", EXIT_INTEGRITY),
        created=created,
        kind=kind,
        source=_text(raw, "source", EXIT_INTEGRITY),
        kernel=_text(raw, "kernel", EXIT_INTEGRITY),
        daemons=_units(raw, EXIT_INTEGRITY),
        dnf_tx=tx,
    )


def scan_snaps(root: Path) -> tuple[list[Snapshot], list[str]]:
    """扫描快照根目录，按创建时间倒序返回可用与损坏两类。"""
    if not root.is_dir():
        raise RollbackError(EXIT_PREFLIGHT, f"快照根目录不存在: {root}")
    good: list[Snapshot] = []
    bad: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            snap = read_snap(entry)
        except RollbackError as exc:
            bad.append(f"{entry.name}: {exc.detail}")
            continue
        if snap is not None:
            good.append(snap)
    good.sort(key=_snap_ts, reverse=True)
    return good, bad


def pick_snap(
    snaps: Sequence[Snapshot],
    broken: Sequence[str],
    to_ver: str | None,
    name: str | None,
    current: str,
) -> Snapshot:
    """按 --snapshot / --to / 默认上一版本选择目标快照。"""
    if name:
        match = [
            snap
            for snap in snaps
            if snap.path.name == name or snap.created.startswith(name)
        ]
        if not match:
            hit = [item for item in broken if item.startswith(name + ":")]
            if hit:
                raise RollbackError(EXIT_INTEGRITY, f"快照损坏: {hit[0]}")
            raise RollbackError(EXIT_TARGET, f"未找到快照: {name}")
        return max(match, key=_snap_ts)
    if to_ver:
        match = [snap for snap in snaps if snap.version == to_ver]
        if not match:
            raise RollbackError(EXIT_TARGET, f"未找到版本快照: {to_ver}")
        return max(match, key=_snap_ts)
    previous = [snap for snap in snaps if snap.version != current]
    if not previous:
        raise RollbackError(
            EXIT_TARGET, f"没有可回滚的历史快照（当前 {current}）"
        )
    return max(previous, key=_snap_ts)


def read_release(path: Path) -> str:
    """读取当前版本号；文件缺失或为空视为前置检查失败。"""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RollbackError(EXIT_PREFLIGHT, f"版本文件不可读: {path}") from exc
    match = VERSION_RE.search(text)
    if match:
        return match.group(0)
    if text:
        return text.splitlines()[0].strip()
    raise RollbackError(EXIT_PREFLIGHT, f"版本文件为空: {path}")


def pick_layer(
    requested: Layer,
    snap: Snapshot,
    kernel: str,
    units: Sequence[str],
) -> tuple[Layer, str]:
    """选择最浅的充分回滚层次。"""
    if requested is Layer.L1 and snap.dnf_tx is None:
        raise RollbackError(EXIT_TARGET, "快照缺少 dnf 事务，无法执行 L1")
    if requested is not Layer.AUTO:
        return requested, "操作者指定层次"
    if snap.kernel != kernel:
        return Layer.L3, "内核不同，需根分区快照"
    if set(snap.daemons) != set(units):
        return Layer.L3, "daemon 集合不同，需根分区快照"
    if snap.dnf_tx is not None:
        return Layer.L1, "存在 dnf 事务，使用事务回滚"
    return Layer.L2, "仅 unit 状态变化，重载并重启"


def _seq_val(
    token: str, values: Mapping[str, str | Sequence[str]]
) -> list[str] | None:
    for key, value in values.items():
        if token == "{" + key + "}" and not isinstance(value, str):
            return [str(item) for item in value]
    return None


def _fill(token: str, values: Mapping[str, str | Sequence[str]]) -> str:
    for key, value in values.items():
        if isinstance(value, str):
            token = token.replace("{" + key + "}", value)
    return token


def expand(
    tmpl: Sequence[str], values: Mapping[str, str | Sequence[str]]
) -> tuple[str, ...]:
    """展开命令模板；序列值（如 units）展开为多个参数。"""
    out: list[str] = []
    for token in tmpl:
        seq = _seq_val(token, values)
        if seq is not None:
            out.extend(seq)
        else:
            out.append(_fill(token, values))
    return tuple(out)


def _l3_steps(
    manifest: Manifest, snap: Snapshot, stamp: str
) -> tuple[Step, ...]:
    cmds = manifest.commands
    if snap.kind is Kind.BTRFS:
        dest = str(manifest.snapshot_root / (SNAP_PREFIX + stamp))
        return (
            Step(
                expand(cmds["l3_btrfs_snap"], {"dest": dest}),
                "回滚前保存当前根子卷",
            ),
            Step(
                expand(cmds["l3_btrfs_default"], {"source": snap.source}),
                f"切换默认子卷到 {snap.source}",
            ),
        )
    return (
        Step(
            expand(cmds["l3_lvm_merge"], {"source": snap.source}),
            f"合并 LVM 快照 {snap.source}",
        ),
    )


def build_steps(
    manifest: Manifest, snap: Snapshot, layer: Layer, stamp: str
) -> tuple[Step, ...]:
    """按层次生成命令序列。"""
    cmds = manifest.commands
    if layer is Layer.L3:
        return _l3_steps(manifest, snap, stamp)
    steps = []
    if layer is Layer.L1:
        tx = str(snap.dnf_tx)
        steps.append(
            Step(expand(cmds["l1_rollback"], {"tx": tx}), "回滚 dnf 事务")
        )
    steps.append(Step(expand(cmds["l2_reload"], {}), "重载 systemd unit"))
    steps.append(
        Step(
            expand(cmds["l2_restart"], {"units": manifest.daemons}),
            f"重启 {len(manifest.daemons)} 个 daemon",
        )
    )
    return tuple(steps)


def expired(
    snaps: Sequence[Snapshot], now: datetime, days: int
) -> tuple[Snapshot, ...]:
    """OS-OPS-021：返回已超过保留期的快照。"""
    limit = now - timedelta(days=days)
    return tuple(snap for snap in snaps if _snap_ts(snap) < limit)


def build_prune(manifest: Manifest, snap: Snapshot) -> Step:
    """生成删除单个过期快照的步骤。"""
    cmds = manifest.commands
    note = f"删除过期快照 {snap.path.name}"
    if snap.kind is Kind.BTRFS:
        argv = expand(cmds["l3_btrfs_del"], {"dest": str(snap.path)})
        return Step(argv, note)
    return Step(expand(cmds["l3_lvm_del"], {"source": snap.source}), note)


def make_plan(
    manifest: Manifest,
    snap: Snapshot,
    layer: Layer,
    kernel: str,
    units: Sequence[str],
    stamp: str,
) -> Plan:
    """生成回滚计划（层次、目标、步骤与依据）。"""
    real, reason = pick_layer(layer, snap, kernel, units)
    steps = build_steps(manifest, snap, real, stamp)
    return Plan(real, snap.version, steps, reason)


def _mount_src(manifest: Manifest, runner: Runner, path: str) -> str:
    proc = runner.run(
        expand(manifest.commands["mount_source"], {"path": path}),
        manifest.timeout_s,
    )
    if not proc.ok:
        detail = proc.err or proc.out or "无输出"
        raise RollbackError(
            EXIT_PREFLIGHT, f"无法读取挂载信息: {path}: {detail}"
        )
    return proc.out


def _device(source: str) -> str:
    """去掉 btrfs 子卷后缀，返回块设备标识。

    findmnt 对 btrfs 子卷输出形如 `/dev/sda2[/@]`，同一文件系统上的
    子卷不是独立分区（OS-OPS-036），必须按方括号前的设备比较。
    """
    return source.split("[", 1)[0].strip()


def mem_check(manifest: Manifest, runner: Runner) -> Check:
    """OS-OPS-036：记忆卷必须是独立挂载，不随根分区回滚。"""
    try:
        root_src = _mount_src(manifest, runner, "/")
        mem_src = _mount_src(manifest, runner, str(manifest.memory_path))
    except RollbackError as exc:
        return Check("memory-volume", False, exc.detail)
    root_dev = _device(root_src)
    mem_dev = _device(mem_src)
    ok = bool(mem_dev) and mem_dev != root_dev
    return Check("memory-volume", ok, f"{mem_dev} / root {root_dev}")


def preflight(
    manifest: Manifest, plan: Plan, snap: Snapshot, runner: Runner
) -> tuple[Check, ...]:
    """执行回滚前置检查，任何失败都阻止命令执行。"""
    checks = [Check("snapshot", snap.path.is_dir(), str(snap.path))]
    pending = manifest.pending_flag.exists()
    checks.append(
        Check(
            "pending-upgrade",
            True,
            "存在升级标记，成功后清除" if pending else "无",
        )
    )
    if plan.layer is not Layer.L3:
        return tuple(checks)
    checks.append(mem_check(manifest, runner))
    if snap.kind is Kind.BTRFS:
        checks.append(
            Check("btrfs-source", Path(snap.source).exists(), snap.source)
        )
    return tuple(checks)


def run_steps(
    steps: Sequence[Step], runner: Runner, timeout_s: float
) -> tuple[int, str, int]:
    """顺序执行步骤。

    首个失败即停止；返回退出码、失败诊断与已生效的步骤数。

    Args:
        steps: 已展开的命令序列。
        runner: 命令执行器。
        timeout_s: 单条命令超时。

    Returns:
        (退出码, 诊断, 已生效步数)；已有步骤生效时退出码为
        EXIT_PARTIAL，否则为 EXIT_FAIL。
    """
    done = 0
    for step in steps:
        proc = runner.run(step.argv, timeout_s)
        if not proc.ok:
            detail = proc.err or proc.out or "无输出"
            code = EXIT_PARTIAL if done else EXIT_FAIL
            return code, f"{step.note} 失败（{proc.code}）: {detail}", done
        done += 1
    return EXIT_OK, "", done


def verify_units(manifest: Manifest, runner: Runner) -> tuple[Check, ...]:
    """OS-OPS-022：12 个 daemon 必须全部 active。"""
    checks = []
    for unit in manifest.daemons:
        proc = runner.run(
            expand(manifest.commands["daemon_active"], {"unit": unit}),
            manifest.timeout_s,
        )
        state = proc.out.strip()
        active = proc.ok and state == "active"
        checks.append(Check(f"unit:{unit}", active, state))
    return tuple(checks)


def write_audit(path: Path, record: Mapping[str, Any]) -> None:
    """append-only 写入一条审计记录并落盘。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    data = text.encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o640)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


_RESULT = {EXIT_OK: "ok", EXIT_PARTIAL: "partial"}


def _record(
    actor: str,
    action: str,
    plan: Plan | None,
    outcome: Outcome,
    comment: str,
    steps: Sequence[Step] = (),
) -> dict[str, Any]:
    source = plan.steps if plan else steps
    argv = [list(step.argv) for step in source]
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
        "action": action,
        "layer": plan.layer.value if plan else "",
        "target": plan.target if plan else "",
        "code": outcome.code,
        "result": _RESULT.get(outcome.code, "fail"),
        "comment": comment,
        "steps": argv,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in outcome.checks
        ],
    }


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _clear_pending(manifest: Manifest) -> None:
    try:
        manifest.pending_flag.unlink(missing_ok=True)
    except OSError as exc:
        raise RollbackError(
            EXIT_FAIL, f"无法清除升级标记: {manifest.pending_flag}: {exc}"
        ) from exc


def _resolve(
    manifest: Manifest, args: argparse.Namespace
) -> tuple[Plan, Snapshot]:
    current = read_release(manifest.release_file)
    snaps, broken = scan_snaps(manifest.snapshot_root)
    snap = pick_snap(snaps, broken, args.to_ver, args.snapshot, current)
    plan = make_plan(
        manifest,
        snap,
        args.layer,
        os.uname().release,
        manifest.daemons,
        _stamp(),
    )
    return plan, snap


def _apply(
    manifest: Manifest, args: argparse.Namespace, runner: Runner
) -> Outcome:
    plan, snap = _resolve(manifest, args)
    checks = preflight(manifest, plan, snap, runner)
    bad = [check for check in checks if not check.ok]
    if args.dry_run:
        detail = "dry-run：未执行任何命令"
        if bad:
            return Outcome(
                EXIT_PREFLIGHT,
                f"{detail}；前置检查未通过: {bad[0].name}",
                plan,
                checks,
            )
        return Outcome(EXIT_OK, detail, plan, checks)
    if bad:
        return Outcome(
            EXIT_PREFLIGHT, f"前置检查未通过: {bad[0].name}", plan, checks
        )
    code, detail, _ = run_steps(plan.steps, runner, manifest.timeout_s)
    if code != EXIT_OK:
        return Outcome(code, detail, plan, checks)
    if plan.layer is Layer.L3:
        return _finish_l3(manifest, args, plan, checks, runner)
    checks = checks + verify_units(manifest, runner)
    bad = [check for check in checks if not check.ok]
    if bad:
        return Outcome(
            EXIT_VERIFY, f"回滚后校验未通过: {bad[0].name}", plan, checks
        )
    try:
        _clear_pending(manifest)
    except RollbackError as exc:
        return Outcome(exc.code, exc.detail, plan, checks)
    detail = "回滚完成"
    if plan.layer is Layer.L1:
        detail += "；%config(noreplace) 配置未回滚（OS-OPS-023）"
    return Outcome(EXIT_OK, detail, plan, checks)


def _finish_l3(
    manifest: Manifest,
    args: argparse.Namespace,
    plan: Plan,
    checks: tuple[Check, ...],
    runner: Runner,
) -> Outcome:
    try:
        _clear_pending(manifest)
    except RollbackError as exc:
        return Outcome(exc.code, exc.detail, plan, checks)
    if args.no_reboot:
        return Outcome(
            EXIT_OK, "根分区已切换，请重启后执行 verify", plan, checks
        )
    proc = runner.run(
        expand(manifest.commands["reboot"], {}), manifest.timeout_s
    )
    if not proc.ok:
        detail = proc.err or proc.out or "无输出"
        return Outcome(
            EXIT_PARTIAL, f"根分区已切换但重启失败: {detail}", plan, checks
        )
    return Outcome(
        EXIT_OK, "根分区已切换，系统重启中；重启后执行 verify", plan, checks
    )


def _show_steps(steps: Sequence[Step]) -> None:
    for index, step in enumerate(steps, 1):
        print(f"  {index}. {step.note}")
        print(f"     $ {' '.join(step.argv)}")


def show_plan(plan: Plan) -> None:
    print(f"PLAN: layer={plan.layer.value} target={plan.target}")
    print(f"  reason: {plan.reason}")
    _show_steps(plan.steps)


def show_checks(checks: Sequence[Check]) -> None:
    for check in checks:
        mark = "ok" if check.ok else "FAIL"
        line = f"  [{mark}] {check.name}"
        if check.detail:
            line += f": {check.detail}"
        print(line)


def _actor(explicit: str | None) -> str:
    return (
        explicit
        or os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or "unknown"
    )


def do_plan(
    manifest: Manifest, args: argparse.Namespace, runner: Runner
) -> int:
    plan, snap = _resolve(manifest, args)
    checks = preflight(manifest, plan, snap, runner)
    show_plan(plan)
    show_checks(checks)
    return EXIT_OK if all(check.ok for check in checks) else EXIT_PREFLIGHT


def do_verify(manifest: Manifest, actor: str, runner: Runner) -> int:
    checks = verify_units(manifest, runner) + (mem_check(manifest, runner),)
    pending = manifest.pending_flag.exists()
    detail = "仍存在" if pending else "已清除"
    checks += (Check("pending-upgrade", not pending, detail),)
    ok = all(check.ok for check in checks)
    outcome = Outcome(EXIT_OK if ok else EXIT_VERIFY, "verify", None, checks)
    record = _record(actor, "verify", None, outcome, "")
    write_audit(manifest.audit_log, record)
    show_checks(checks)
    return outcome.code


def _prune(
    manifest: Manifest, args: argparse.Namespace, runner: Runner
) -> tuple[Outcome, tuple[Step, ...]]:
    snaps, _ = scan_snaps(manifest.snapshot_root)
    now = datetime.now(timezone.utc)
    stale = expired(snaps, now, manifest.retention_days)
    steps = tuple(build_prune(manifest, snap) for snap in stale)
    _show_steps(steps)
    check = Check("expired", True, f"{len(steps)} 个快照")
    if args.dry_run:
        detail = "dry-run：未删除任何快照"
        return Outcome(EXIT_OK, detail, None, (check,)), ()
    if not steps:
        return Outcome(EXIT_OK, "无过期快照", None, (check,)), ()
    code, detail, _ = run_steps(steps, runner, manifest.timeout_s)
    text = detail or "过期快照已清理"
    return Outcome(code, text, None, (check,)), steps


def do_prune(
    manifest: Manifest, args: argparse.Namespace, runner: Runner
) -> int:
    actor = _actor(args.actor)
    try:
        with Lock(manifest.lock_file):
            outcome, steps = _prune(manifest, args, runner)
    except RollbackError as exc:
        outcome = Outcome(exc.code, exc.detail)
        steps = ()
        write_audit(
            manifest.audit_log,
            _record(actor, "prune", None, outcome, "", steps),
        )
        print(f"airy-rollback: {exc.detail}", file=sys.stderr)
        return exc.code
    write_audit(
        manifest.audit_log,
        _record(actor, "prune", None, outcome, "", steps),
    )
    show_checks(outcome.checks)
    print(f"airy-rollback: {outcome.detail}")
    return outcome.code


def do_apply(
    manifest: Manifest, args: argparse.Namespace, runner: Runner
) -> int:
    actor = _actor(args.actor)
    try:
        with Lock(manifest.lock_file):
            outcome = _apply(manifest, args, runner)
    except RollbackError as exc:
        outcome = Outcome(exc.code, exc.detail)
        write_audit(
            manifest.audit_log,
            _record(actor, "apply", None, outcome, args.comment),
        )
        raise
    write_audit(
        manifest.audit_log,
        _record(actor, "apply", outcome.plan, outcome, args.comment),
    )
    if outcome.plan:
        show_plan(outcome.plan)
    show_checks(outcome.checks)
    print(f"airy-rollback: {outcome.detail}")
    return outcome.code


def _target_args(sub: argparse.ArgumentParser) -> None:
    group = sub.add_mutually_exclusive_group()
    group.add_argument("--to", dest="to_ver", metavar="VERSION")
    group.add_argument("--snapshot", metavar="NAME")
    sub.add_argument(
        "--layer",
        type=Layer,
        choices=[item.value for item in Layer],
        default=Layer.AUTO,
        help="回滚层次（默认 auto：按快照元数据选择）",
    )


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="airy-rollback",
        description="AirymaxOS 版本级回滚编排器",
        epilog="无子命令等价于 apply --to previous。",
    )
    parser.add_argument("--manifest", type=Path, default=None)
    subs = parser.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", type=Path, default=argparse.SUPPRESS)

    subs.add_parser("check", parents=[common], help="校验清单契约")

    plan = subs.add_parser("plan", parents=[common], help="只读生成回滚计划")
    _target_args(plan)

    apply = subs.add_parser("apply", parents=[common], help="执行回滚")
    _target_args(apply)
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--no-reboot", action="store_true")
    apply.add_argument("--comment", default="")
    apply.add_argument("--actor", default=None)

    verify = subs.add_parser("verify", parents=[common], help="回滚后校验")
    verify.add_argument("--actor", default=None)

    prune = subs.add_parser("prune", parents=[common], help="清理过期快照")
    prune.add_argument("--dry-run", action="store_true")
    prune.add_argument("--actor", default=None)
    return parser


def _defaults(args: argparse.Namespace) -> argparse.Namespace:
    if args.cmd is not None:
        return args
    args.cmd = "apply"
    args.to_ver = None
    args.snapshot = None
    args.layer = Layer.AUTO
    args.dry_run = False
    args.no_reboot = False
    args.comment = ""
    args.actor = None
    return args


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    args = _defaults(build_parser().parse_args(argv))
    if yaml is None:
        print("ERROR: PyYAML 不可用，请安装 pyyaml", file=sys.stderr)
        return EXIT_USAGE
    path = args.manifest or DEFAULT_MANIFEST
    try:
        manifest = load_manifest(path)
        checks = check_manifest(manifest)
        failed = [check for check in checks if not check.ok]
        if failed:
            show_checks(checks)
            return EXIT_USAGE
        runner = Subproc()
        if args.cmd == "check":
            show_checks(checks)
            return EXIT_OK
        if args.cmd == "plan":
            return do_plan(manifest, args, runner)
        if args.cmd == "verify":
            return do_verify(manifest, _actor(args.actor), runner)
        if args.cmd == "prune":
            return do_prune(manifest, args, runner)
        return do_apply(manifest, args, runner)
    except RollbackError as exc:
        print(f"airy-rollback: {exc.detail}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"airy-rollback: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
