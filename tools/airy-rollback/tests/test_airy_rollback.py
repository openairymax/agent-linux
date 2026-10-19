# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/airy-rollback 单元测试：清单、快照、层次决策与执行编排。

覆盖 01-deployment.md §11 的三层回滚契约、OS-OPS-021 保留期、
OS-OPS-022 回滚后校验、OS-OPS-023 配置豁免与 OS-OPS-036 记忆卷。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

import airy_rollback as ar
import rollback_types as rt

REAL = Path(__file__).resolve().parents[1] / "manifest.yaml"
KERNEL = os.uname().release
UNITS = ar.load_manifest(REAL).daemons
NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _man(base: Path) -> rt.Manifest:
    return replace(
        ar.load_manifest(REAL),
        release_file=base / "release",
        snapshot_root=base / "snaps",
        audit_log=base / "state/audit.jsonl",
        pending_flag=base / "state/pending",
        lock_file=base / "state/lock",
        memory_path=base / "mem",
    )


def _raw(base: Path, **over: object) -> Path:
    raw = yaml.safe_load(REAL.read_text(encoding="utf-8"))
    raw.update(over)
    base.mkdir(parents=True, exist_ok=True)
    path = base / "manifest.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _cfg(man: rt.Manifest) -> Path:
    return _raw(
        man.release_file.parent,
        release_file=str(man.release_file),
        snapshot_root=str(man.snapshot_root),
        audit_log=str(man.audit_log),
        pending_flag=str(man.pending_flag),
        lock_file=str(man.lock_file),
        memory_path=str(man.memory_path),
        daemons=list(man.daemons),
        commands={key: list(argv) for key, argv in man.commands.items()},
        retention_days=man.retention_days,
        timeout_s=man.timeout_s,
    )


def _mk(root: Path, name: str, drop: tuple[str, ...] = (), **over: object):
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {
        "version": "1.0.0",
        "created": "2026-01-01T00:00:00Z",
        "kind": "btrfs",
        "source": str(root.parent / name),
        "kernel": KERNEL,
        "daemons": list(UNITS),
    }
    meta.update(over)
    for key in drop:
        meta.pop(key, None)
    (path / ar.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    snap = ar.read_snap(path)
    assert snap is not None
    return snap


def _obj(base: Path, **over: object) -> rt.Snapshot:
    data: dict[str, object] = {
        "path": base / "snap",
        "version": "1.0.0",
        "created": "2026-01-01T00:00:00Z",
        "kind": rt.Kind.BTRFS,
        "source": str(base / "src"),
        "kernel": KERNEL,
        "daemons": UNITS,
        "dnf_tx": None,
    }
    data.update(over)
    return rt.Snapshot(**data)


def _args(**over: object) -> argparse.Namespace:
    data: dict[str, object] = {
        "to_ver": None,
        "snapshot": None,
        "layer": rt.Layer.AUTO,
        "dry_run": False,
        "no_reboot": False,
        "comment": "",
        "actor": None,
    }
    data.update(over)
    return argparse.Namespace(**data)


def _box(base: Path, kind: str = "btrfs", tx=None, kernel=KERNEL,
         pending=False) -> tuple[rt.Manifest, rt.Snapshot]:
    man = _man(base)
    man.snapshot_root.mkdir(parents=True, exist_ok=True)
    man.audit_log.parent.mkdir(parents=True, exist_ok=True)
    man.release_file.parent.mkdir(parents=True, exist_ok=True)
    man.release_file.write_text("2.0.0\n", encoding="utf-8")
    if pending:
        man.pending_flag.write_text("", encoding="utf-8")
    src = base / ("subvol" if kind == "btrfs" else "lv")
    if kind == "btrfs":
        src.mkdir(parents=True, exist_ok=True)
    snap = _mk(
        man.snapshot_root, "snap-a", kind=kind, source=str(src),
        kernel=kernel, dnf_tx=tx,
    )
    return man, snap


class FakeRunner:
    """按 argv 精确匹配的受控执行器，未命中时返回 default。"""

    def __init__(self, table=None, default=None):
        self.table = table or {}
        self.default = default or rt.Proc(0, "active", "")
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, timeout_s):
        key = tuple(argv)
        self.calls.append(key)
        return self.table.get(key, self.default)


def _ftab(man: rt.Manifest, mem: str = "/dev/sdb1",
          root: str = "/dev/sda2[/@]") -> dict:
    table = {
        ar.expand(man.commands["mount_source"], {"path": "/"}):
            rt.Proc(0, root),
        ar.expand(man.commands["mount_source"],
                  {"path": str(man.memory_path)}): rt.Proc(0, mem),
    }
    for unit in man.daemons:
        table[ar.expand(man.commands["daemon_active"], {"unit": unit})] = (
            rt.Proc(0, "active")
        )
    return table


# ─── 清单加载 ───


def test_man_ok():
    man = ar.load_manifest(REAL)
    assert man.version == "1.0.0"
    assert len(man.daemons) == 12
    assert man.retention_days == 7
    assert man.timeout_s == 30.0
    assert all(path.is_absolute() for path in (
        man.release_file, man.snapshot_root, man.audit_log,
        man.pending_flag, man.lock_file, man.memory_path,
    ))


def test_man_yaml(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(tmp_path / "absent.yaml")
    assert info.value.code == rt.EXIT_USAGE
    assert "清单不可读" in info.value.detail


def test_man_map(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(path)
    assert "必须是映射" in info.value.detail


def test_man_field(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(_raw(tmp_path, release_file=None))
    assert "release_file" in info.value.detail


def test_man_daem(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(_raw(tmp_path, daemons=[]))
    assert "daemons" in info.value.detail


def test_man_num(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(_raw(tmp_path, retention_days="7"))
    assert "retention_days" in info.value.detail


def test_man_cmds(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(_raw(tmp_path, commands="x"))
    assert "commands" in info.value.detail


def test_man_pyyaml(monkeypatch):
    monkeypatch.setattr(ar, "yaml", None)
    with pytest.raises(rt.RollbackError) as info:
        ar.load_manifest(REAL)
    assert info.value.code == rt.EXIT_USAGE
    assert "PyYAML" in info.value.detail


# ─── 清单语义校验 ───


def _chk(man: rt.Manifest) -> dict[str, rt.Check]:
    return {item.name: item for item in ar.check_manifest(man)}


def test_chk_ok(tmp_path):
    checks = _chk(_man(tmp_path))
    assert list(checks) == [
        "version", "paths", "daemons", "retention", "timeout", "commands",
    ]
    assert all(item.ok for item in checks.values())


def test_chk_ver(tmp_path):
    assert not _chk(replace(_man(tmp_path), version="1.0"))["version"].ok


def test_chk_path(tmp_path):
    man = replace(_man(tmp_path), release_file=Path("release"))
    assert not _chk(man)["paths"].ok


def test_chk_units(tmp_path):
    man = replace(_man(tmp_path), daemons=UNITS[:-1])
    assert not _chk(man)["daemons"].ok


def test_chk_dup(tmp_path):
    man = replace(_man(tmp_path), daemons=UNITS[:-1] + (UNITS[0],))
    assert not _chk(man)["daemons"].ok


def test_chk_svc(tmp_path):
    man = replace(_man(tmp_path), daemons=UNITS[:-1] + ("agentrt-x",))
    assert not _chk(man)["daemons"].ok


def test_chk_ret(tmp_path):
    man = replace(_man(tmp_path), retention_days=0)
    assert not _chk(man)["retention"].ok


def test_chk_tmo(tmp_path):
    assert not _chk(replace(_man(tmp_path), timeout_s=0.0))["timeout"].ok


def test_chk_miss(tmp_path):
    man = _man(tmp_path)
    cmds = dict(man.commands)
    del cmds["reboot"]
    check = _chk(replace(man, commands=cmds))["commands"]
    assert not check.ok
    assert "缺少模板" in check.detail


def test_chk_ph(tmp_path):
    man = _man(tmp_path)
    cmds = dict(man.commands)
    cmds["l1_rollback"] = ("dnf", "history", "rollback")
    check = _chk(replace(man, commands=cmds))["commands"]
    assert "缺少占位符" in check.detail


def test_chk_extra(tmp_path):
    man = _man(tmp_path)
    cmds = dict(man.commands)
    cmds["extra"] = ("tool", "{bogus}")
    check = _chk(replace(man, commands=cmds))["commands"]
    assert "未知占位符" in check.detail


# ─── 快照读取 ───


def test_snap_none(tmp_path):
    assert ar.read_snap(tmp_path) is None


def test_snap_good(tmp_path):
    snap = _mk(tmp_path, "s1", dnf_tx=42)
    assert snap.version == "1.0.0"
    assert snap.kind is rt.Kind.BTRFS
    assert snap.dnf_tx == 42
    assert snap.daemons == UNITS


def test_snap_json(tmp_path):
    path = tmp_path / "s1"
    path.mkdir()
    (path / ar.META_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(rt.RollbackError) as info:
        ar.read_snap(path)
    assert info.value.code == rt.EXIT_INTEGRITY


def test_snap_obj(tmp_path):
    path = tmp_path / "s1"
    path.mkdir()
    (path / ar.META_NAME).write_text("[]", encoding="utf-8")
    with pytest.raises(rt.RollbackError) as info:
        ar.read_snap(path)
    assert "必须是对象" in info.value.detail


def test_snap_kind(tmp_path):
    path = tmp_path / "s1"
    path.mkdir()
    (path / ar.META_NAME).write_text(
        json.dumps({"kind": "zfs"}), encoding="utf-8"
    )
    with pytest.raises(rt.RollbackError) as info:
        ar.read_snap(path)
    assert "kind 非法" in info.value.detail


def test_snap_ts(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        _mk(tmp_path, "s1", created="yesterday")
    assert "created 非法" in info.value.detail


def test_snap_tx(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        _mk(tmp_path, "s1", dnf_tx="42")
    assert "dnf_tx" in info.value.detail


def test_snap_ver(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        _mk(tmp_path, "s1", drop=("version",))
    assert "version" in info.value.detail


# ─── 快照扫描 ───


def test_scan_root(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.scan_snaps(tmp_path / "absent")
    assert info.value.code == rt.EXIT_PREFLIGHT


def test_scan_sort(tmp_path):
    _mk(tmp_path, "old", created="2026-01-01T00:00:00Z")
    _mk(tmp_path, "new", created="2026-01-03T00:00:00Z")
    _mk(tmp_path, "mid", created="2026-01-02T00:00:00Z")
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    good, bad = ar.scan_snaps(tmp_path)
    assert [item.path.name for item in good] == ["new", "mid", "old"]
    assert bad == []


def test_scan_bad(tmp_path):
    path = tmp_path / "broken"
    path.mkdir()
    (path / ar.META_NAME).write_text("{", encoding="utf-8")
    good, bad = ar.scan_snaps(tmp_path)
    assert good == []
    assert bad and bad[0].startswith("broken:")


# ─── 快照选择 ───


def test_pick_name(tmp_path):
    snap = _obj(tmp_path, path=tmp_path / "named")
    assert ar.pick_snap([snap], [], None, "named", "2.0.0") is snap


def test_pick_pre(tmp_path):
    old = _obj(tmp_path, path=tmp_path / "a",
               created="2026-01-02T01:00:00Z")
    new = _obj(tmp_path, path=tmp_path / "b",
               created="2026-01-02T05:00:00Z")
    got = ar.pick_snap([old, new], [], None, "2026-01-02", "2.0.0")
    assert got is new


def test_pick_none(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.pick_snap([], [], None, "nope", "2.0.0")
    assert info.value.code == rt.EXIT_TARGET


def test_pick_bad(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.pick_snap([], ["snap-x: 元数据损坏"], None, "snap-x", "2.0.0")
    assert info.value.code == rt.EXIT_INTEGRITY


def test_pick_ver(tmp_path):
    snap = _obj(tmp_path, version="1.0.0")
    assert ar.pick_snap([snap], [], "1.0.0", None, "2.0.0") is snap


def test_pick_vmiss(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.pick_snap([], [], "1.0.0", None, "2.0.0")
    assert info.value.code == rt.EXIT_TARGET


def test_pick_prev(tmp_path):
    old = _obj(tmp_path, path=tmp_path / "a", version="1.0.0",
               created="2026-01-01T00:00:00Z")
    cur = _obj(tmp_path, path=tmp_path / "b", version="2.0.0",
               created="2026-01-02T00:00:00Z")
    assert ar.pick_snap([old, cur], [], None, None, "2.0.0") is old


def test_pick_same(tmp_path):
    cur = _obj(tmp_path, version="2.0.0")
    with pytest.raises(rt.RollbackError) as info:
        ar.pick_snap([cur], [], None, None, "2.0.0")
    assert info.value.code == rt.EXIT_TARGET
    assert "没有可回滚" in info.value.detail


# ─── 层次决策 ───


def test_layer_l1(tmp_path):
    layer, reason = ar.pick_layer(
        rt.Layer.AUTO, _obj(tmp_path, dnf_tx=42), KERNEL, UNITS
    )
    assert layer is rt.Layer.L1
    assert "dnf" in reason


def test_layer_notx(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.pick_layer(rt.Layer.L1, _obj(tmp_path), KERNEL, UNITS)
    assert info.value.code == rt.EXIT_TARGET


def test_layer_fix(tmp_path):
    layer, reason = ar.pick_layer(
        rt.Layer.L2, _obj(tmp_path), KERNEL, UNITS
    )
    assert layer is rt.Layer.L2
    assert reason == "操作者指定层次"


def test_layer_ker(tmp_path):
    layer, reason = ar.pick_layer(
        rt.Layer.AUTO, _obj(tmp_path, kernel="0.0.1-alt"), KERNEL, UNITS
    )
    assert layer is rt.Layer.L3
    assert "内核不同" in reason


def test_layer_dmn(tmp_path):
    layer, reason = ar.pick_layer(
        rt.Layer.AUTO, _obj(tmp_path), KERNEL, UNITS[:-1]
    )
    assert layer is rt.Layer.L3
    assert "daemon" in reason


def test_layer_l2(tmp_path):
    layer, reason = ar.pick_layer(
        rt.Layer.AUTO, _obj(tmp_path), KERNEL, UNITS
    )
    assert layer is rt.Layer.L2
    assert "unit" in reason


def test_layer_l3(tmp_path):
    layer, _ = ar.pick_layer(
        rt.Layer.L3, _obj(tmp_path, kernel="0.0.1-alt"), KERNEL, UNITS
    )
    assert layer is rt.Layer.L3


# ─── 模板展开 ───


def test_expand_str():
    got = ar.expand(("dnf", "rollback", "{tx}"), {"tx": "42"})
    assert got == ("dnf", "rollback", "42")


def test_expand_seq():
    got = ar.expand(("systemctl", "restart", "{units}"), {"units": UNITS})
    assert got == ("systemctl", "restart") + UNITS


# ─── 步骤生成 ───


def test_steps_l1(tmp_path):
    man = _man(tmp_path)
    steps = ar.build_steps(man, _obj(tmp_path, dnf_tx=42), rt.Layer.L1, "S")
    assert steps[0].argv == ("dnf", "-y", "history", "rollback", "42")
    assert steps[1].argv == ("systemctl", "daemon-reload")
    assert steps[2].argv == ("systemctl", "restart") + man.daemons
    assert "12 个 daemon" in steps[2].note


def test_steps_l2(tmp_path):
    steps = ar.build_steps(
        _man(tmp_path), _obj(tmp_path), rt.Layer.L2, "S"
    )
    assert len(steps) == 2
    assert steps[0].argv == ("systemctl", "daemon-reload")


def test_steps_btr(tmp_path):
    man = _man(tmp_path)
    snap = _obj(tmp_path, source="/sub/@old")
    steps = ar.build_steps(man, snap, rt.Layer.L3, "20260101T000000Z")
    dest = str(man.snapshot_root / "pre-rollback-20260101T000000Z")
    assert steps[0].argv == ("btrfs", "subvolume", "snapshot", "/", dest)
    assert steps[1].argv == ("btrfs", "subvolume", "set-default", "/sub/@old")


def test_steps_lvm(tmp_path):
    snap = _obj(tmp_path, kind=rt.Kind.LVM, source="vg0/root-snap")
    steps = ar.build_steps(_man(tmp_path), snap, rt.Layer.L3, "S")
    assert len(steps) == 1
    assert steps[0].argv == ("lvconvert", "--merge", "vg0/root-snap")


def test_plan_auto(tmp_path):
    plan = ar.make_plan(
        _man(tmp_path), _obj(tmp_path, dnf_tx=7), rt.Layer.AUTO,
        KERNEL, UNITS, "S",
    )
    assert plan.layer is rt.Layer.L1
    assert plan.target == "1.0.0"
    assert plan.steps[0].argv[-1] == "7"


# ─── 版本读取 ───


def test_rel_ok(tmp_path):
    path = tmp_path / "release"
    path.write_text("1.2.3\n", encoding="utf-8")
    assert ar.read_release(path) == "1.2.3"


def test_rel_scan(tmp_path):
    path = tmp_path / "release"
    path.write_text("AirymaxOS 1.2.3 (build 7)\n", encoding="utf-8")
    assert ar.read_release(path) == "1.2.3"


def test_rel_miss(tmp_path):
    with pytest.raises(rt.RollbackError) as info:
        ar.read_release(tmp_path / "absent")
    assert info.value.code == rt.EXIT_PREFLIGHT


def test_rel_empty(tmp_path):
    path = tmp_path / "release"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(rt.RollbackError) as info:
        ar.read_release(path)
    assert "为空" in info.value.detail


def test_rel_text(tmp_path):
    path = tmp_path / "release"
    path.write_text("rolling\nnext\n", encoding="utf-8")
    assert ar.read_release(path) == "rolling"


# ─── 步骤执行 ───


def test_run_ok():
    runner = FakeRunner()
    steps = (rt.Step(("a",), "one"), rt.Step(("b",), "two"))
    assert ar.run_steps(steps, runner, 5.0) == (rt.EXIT_OK, "", 2)
    assert runner.calls == [("a",), ("b",)]


def test_run_first():
    runner = FakeRunner({("a",): rt.Proc(1, "", "boom")})
    code, detail, done = ar.run_steps(
        (rt.Step(("a",), "第一步"),), runner, 5.0
    )
    assert (code, done) == (rt.EXIT_FAIL, 0)
    assert "第一步" in detail and "boom" in detail


def test_run_part():
    runner = FakeRunner({("b",): rt.Proc(2, "out", "")})
    steps = (rt.Step(("a",), "one"), rt.Step(("b",), "two"))
    code, detail, done = ar.run_steps(steps, runner, 5.0)
    assert (code, done) == (rt.EXIT_PARTIAL, 1)
    assert "two" in detail and "out" in detail


def test_run_empty():
    assert ar.run_steps((), FakeRunner(), 5.0) == (rt.EXIT_OK, "", 0)


# ─── 回滚后校验 ───


def test_verify_ok(tmp_path):
    checks = ar.verify_units(_man(tmp_path), FakeRunner())
    assert len(checks) == 12
    assert all(item.ok for item in checks)


def test_verify_bad(tmp_path):
    man = _man(tmp_path)
    bad = ar.expand(man.commands["daemon_active"], {"unit": man.daemons[3]})
    checks = ar.verify_units(man, FakeRunner({bad: rt.Proc(0, "inactive")}))
    failed = [item for item in checks if not item.ok]
    assert len(failed) == 1
    assert failed[0].name == f"unit:{man.daemons[3]}"


def test_verify_err(tmp_path):
    man = _man(tmp_path)
    bad = ar.expand(man.commands["daemon_active"], {"unit": man.daemons[0]})
    checks = ar.verify_units(man, FakeRunner({bad: rt.Proc(1, "unknown")}))
    assert not checks[0].ok
    assert checks[0].detail == "unknown"


def test_mem_ok(tmp_path):
    man = _man(tmp_path)
    assert ar.mem_check(man, FakeRunner(_ftab(man))).ok


def test_mem_same(tmp_path):
    man = _man(tmp_path)
    check = ar.mem_check(
        man, FakeRunner(_ftab(man, mem="/dev/sda2[/@mem]"))
    )
    assert not check.ok


def test_mem_fail(tmp_path):
    man = _man(tmp_path)
    key = ar.expand(man.commands["mount_source"],
                    {"path": str(man.memory_path)})
    check = ar.mem_check(man, FakeRunner({key: rt.Proc(1, "", "no mount")}))
    assert not check.ok
    assert "no mount" in check.detail


def test_mem_empty(tmp_path):
    man = _man(tmp_path)
    check = ar.mem_check(man, FakeRunner(_ftab(man, mem="")))
    assert not check.ok


# ─── 前置检查 ───


def test_pre_l1(tmp_path):
    snap = _obj(tmp_path)
    snap.path.mkdir()
    plan = rt.Plan(rt.Layer.L1, "1.0.0", (), "test")
    checks = ar.preflight(_man(tmp_path), plan, snap, FakeRunner())
    assert [item.name for item in checks] == ["snapshot", "pending-upgrade"]
    assert checks[0].ok


def test_pre_l3(tmp_path):
    man, snap = _box(tmp_path)
    plan = rt.Plan(rt.Layer.L3, "1.0.0", (), "test")
    checks = ar.preflight(man, plan, snap, FakeRunner(_ftab(man)))
    assert [item.name for item in checks] == [
        "snapshot", "pending-upgrade", "memory-volume", "btrfs-source",
    ]
    assert all(item.ok for item in checks)


def test_pre_nosnap(tmp_path):
    man, snap = _box(tmp_path)
    shutil.rmtree(snap.path)
    plan = rt.Plan(rt.Layer.L1, "1.0.0", (), "test")
    checks = ar.preflight(man, plan, snap, FakeRunner())
    assert not checks[0].ok


def test_pre_pend(tmp_path):
    man, snap = _box(tmp_path, pending=True)
    plan = rt.Plan(rt.Layer.L1, "1.0.0", (), "test")
    checks = ar.preflight(man, plan, snap, FakeRunner())
    assert "存在升级标记" in checks[1].detail


# ─── 审计与保留期 ───


def test_audit_app(tmp_path):
    path = tmp_path / "audit.jsonl"
    ar.write_audit(path, {"a": 1})
    ar.write_audit(path, {"b": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": 2}]


def test_audit_mode(tmp_path):
    path = tmp_path / "audit.jsonl"
    ar.write_audit(path, {"a": 1})
    mode = path.stat().st_mode & 0o777
    assert mode & 0o007 == 0
    assert mode & 0o040


def test_expire_old(tmp_path):
    old = _obj(tmp_path, created="2026-01-01T00:00:00Z")
    fresh = _obj(tmp_path, created="2026-01-05T00:00:00Z")
    got = ar.expired([old, fresh], NOW, 7)
    assert got == (old,)


def test_prune_btr(tmp_path):
    snap = _obj(tmp_path)
    step = ar.build_prune(_man(tmp_path), snap)
    assert step.argv == ("btrfs", "subvolume", "delete", str(snap.path))


def test_prune_lvm(tmp_path):
    snap = _obj(tmp_path, kind=rt.Kind.LVM, source="vg0/snap")
    step = ar.build_prune(_man(tmp_path), snap)
    assert step.argv == ("lvremove", "-f", "vg0/snap")


# ─── apply 编排 ───


def test_apply_dry(tmp_path):
    man, _ = _box(tmp_path, tx=42)
    runner = FakeRunner()
    out = ar._apply(man, _args(dry_run=True), runner)
    assert out.code == rt.EXIT_OK
    assert "dry-run" in out.detail
    assert runner.calls == []


def test_apply_pre(tmp_path):
    man, _ = _box(tmp_path, kernel="0.0.1-alt")
    table = _ftab(man, mem="/dev/sda2[/@mem]")
    out = ar._apply(man, _args(dry_run=True), FakeRunner(table))
    assert out.code == rt.EXIT_PREFLIGHT
    assert "dry-run" in out.detail
    assert "memory-volume" in out.detail


def test_apply_l1(tmp_path):
    man, _ = _box(tmp_path, tx=42, pending=True)
    out = ar._apply(man, _args(), FakeRunner(_ftab(man)))
    assert out.code == rt.EXIT_OK
    assert out.plan.layer is rt.Layer.L1
    assert "OS-OPS-023" in out.detail
    assert not man.pending_flag.exists()


def test_apply_l2(tmp_path):
    man, _ = _box(tmp_path)
    out = ar._apply(man, _args(), FakeRunner(_ftab(man)))
    assert out.code == rt.EXIT_OK
    assert out.plan.layer is rt.Layer.L2
    assert "OS-OPS-023" not in out.detail


def test_apply_ver(tmp_path):
    man, _ = _box(tmp_path, tx=42)
    bad = ar.expand(man.commands["daemon_active"], {"unit": man.daemons[0]})
    table = _ftab(man)
    table[bad] = rt.Proc(0, "inactive")
    out = ar._apply(man, _args(), FakeRunner(table))
    assert out.code == rt.EXIT_VERIFY
    assert "回滚后校验未通过" in out.detail


def test_apply_fail(tmp_path):
    man, _ = _box(tmp_path, tx=42)
    table = _ftab(man)
    table[("dnf", "-y", "history", "rollback", "42")] = rt.Proc(1, "", "x")
    out = ar._apply(man, _args(), FakeRunner(table))
    assert out.code == rt.EXIT_FAIL
    assert man.pending_flag.exists() is False


def test_apply_part(tmp_path):
    man, _ = _box(tmp_path, tx=42)
    table = _ftab(man)
    table[("systemctl", "daemon-reload")] = rt.Proc(1, "", "busy")
    out = ar._apply(man, _args(), FakeRunner(table))
    assert out.code == rt.EXIT_PARTIAL
    assert "busy" in out.detail


def test_apply_l3(tmp_path):
    man, _ = _box(tmp_path, kernel="0.0.1-alt", pending=True)
    runner = FakeRunner(_ftab(man))
    out = ar._apply(man, _args(no_reboot=True), runner)
    assert out.code == rt.EXIT_OK
    assert out.plan.layer is rt.Layer.L3
    assert "请重启后执行 verify" in out.detail
    assert not man.pending_flag.exists()


def test_apply_boot(tmp_path):
    man, _ = _box(tmp_path, kernel="0.0.1-alt")
    table = _ftab(man)
    table[ar.expand(man.commands["reboot"], {})] = rt.Proc(1, "", "boom")
    out = ar._apply(man, _args(), FakeRunner(table))
    assert out.code == rt.EXIT_PARTIAL
    assert "重启失败" in out.detail


def test_apply_reb(tmp_path):
    man, _ = _box(tmp_path, kernel="0.0.1-alt")
    out = ar._apply(man, _args(), FakeRunner(_ftab(man)))
    assert out.code == rt.EXIT_OK
    assert "系统重启中" in out.detail


# ─── prune 编排 ───


def test_prune_dry(tmp_path):
    man, _ = _box(tmp_path)
    runner = FakeRunner()
    code = ar.do_prune(man, _args(dry_run=True, actor="t"), runner)
    assert code == rt.EXIT_OK
    assert runner.calls == []
    record = json.loads(
        man.audit_log.read_text(encoding="utf-8").splitlines()[0]
    )
    assert record["action"] == "prune" and record["steps"] == []


def test_prune_run(tmp_path):
    man, _ = _box(tmp_path)
    runner = FakeRunner()
    code = ar.do_prune(man, _args(actor="t"), runner)
    assert code == rt.EXIT_OK
    assert runner.calls == [
        ("btrfs", "subvolume", "delete", str(man.snapshot_root / "snap-a"))
    ]


def test_prune_none(tmp_path):
    man, _ = _box(tmp_path)
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _mk(man.snapshot_root, "fresh", created=fresh)
    shutil.rmtree(man.snapshot_root / "snap-a")
    out, steps = ar._prune(man, _args(), FakeRunner())
    assert out.code == rt.EXIT_OK
    assert out.detail == "无过期快照"
    assert steps == ()


def test_prune_busy(tmp_path):
    man, _ = _box(tmp_path)
    man.lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(man.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert ar.do_prune(man, _args(actor="t"), FakeRunner()) == (
            rt.EXIT_BUSY
        )
    finally:
        os.close(fd)


# ─── 命令行 ───


def test_cli_check(capsys):
    assert ar.main(["--manifest", str(REAL), "check"]) == rt.EXIT_OK
    out = capsys.readouterr().out
    assert "[ok] version" in out and "[ok] commands" in out


def test_cli_help():
    with pytest.raises(SystemExit) as info:
        ar.main(["--help"])
    assert info.value.code == 0


def test_cli_bad():
    with pytest.raises(SystemExit) as info:
        ar.main(["nope"])
    assert info.value.code == 2


def test_cli_excl():
    with pytest.raises(SystemExit) as info:
        ar.main(["plan", "--to", "1.0.0", "--snapshot", "x"])
    assert info.value.code == 2


def test_cli_noyaml(monkeypatch, capsys):
    monkeypatch.setattr(ar, "yaml", None)
    assert ar.main(["check"]) == rt.EXIT_USAGE
    assert "PyYAML" in capsys.readouterr().err


def test_cli_badman(tmp_path, capsys):
    path = _raw(tmp_path, version="bad")
    assert ar.main(["--manifest", str(path), "check"]) == rt.EXIT_USAGE
    assert "[FAIL] version" in capsys.readouterr().out


def test_cli_plan(tmp_path, capsys):
    man, _ = _box(tmp_path, tx=42)
    code = ar.main(["--manifest", str(_cfg(man)), "plan"])
    assert code == rt.EXIT_OK
    assert "layer=l1" in capsys.readouterr().out


def test_cli_apply(tmp_path, monkeypatch):
    man, _ = _box(tmp_path, tx=42)
    monkeypatch.setattr(ar, "Subproc", lambda: FakeRunner(_ftab(man)))
    code = ar.main(["--manifest", str(_cfg(man)), "apply", "--dry-run"])
    assert code == rt.EXIT_OK


def test_cli_prune(tmp_path):
    man, _ = _box(tmp_path)
    assert ar.main(["--manifest", str(_cfg(man)), "prune", "--dry-run"]) == (
        rt.EXIT_OK
    )


def test_cli_verify(tmp_path, monkeypatch):
    man, _ = _box(tmp_path)
    monkeypatch.setattr(ar, "Subproc", lambda: FakeRunner(_ftab(man)))
    assert ar.main(["--manifest", str(_cfg(man)), "verify"]) == rt.EXIT_OK


# ─── 执行器、锁与审计记录 ───


def test_sub_ok():
    assert ar.Subproc().run(["true"], 5.0).ok


def test_sub_err():
    proc = ar.Subproc().run(["false"], 5.0)
    assert not proc.ok and proc.code == 1


def test_sub_tmo():
    proc = ar.Subproc().run(
        [sys.executable, "-c", "import time; time.sleep(2)"], 0.1
    )
    assert proc.code == 124
    assert "超时" in proc.err


def test_sub_miss():
    proc = ar.Subproc().run(["/nonexistent/airy-tool"], 5.0)
    assert proc.code == 127
    assert "不可用" in proc.err


def test_lock_busy(tmp_path):
    path = tmp_path / "lock"
    with ar.Lock(path):
        with pytest.raises(rt.RollbackError) as info:
            with ar.Lock(path):
                pass
    assert info.value.code == rt.EXIT_BUSY


def test_lock_free(tmp_path):
    path = tmp_path / "lock"
    with ar.Lock(path):
        pass
    with ar.Lock(path):
        pass


def test_lock_mkdir(tmp_path):
    path = tmp_path / "a" / "b" / "lock"
    with ar.Lock(path):
        assert path.exists()


def test_rec_part():
    plan = rt.Plan(rt.Layer.L1, "1.0.0", (), "r")
    out = rt.Outcome(rt.EXIT_PARTIAL, "x", plan)
    record = ar._record("t", "apply", plan, out, "note")
    assert record["result"] == "partial"
    assert record["layer"] == "l1" and record["target"] == "1.0.0"


def test_rec_fail():
    out = rt.Outcome(rt.EXIT_FAIL, "x")
    record = ar._record("t", "apply", None, out, "")
    assert record["result"] == "fail"
    assert record["layer"] == "" and record["steps"] == []
