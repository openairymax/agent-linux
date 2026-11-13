#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
release_manifest.py — AirymaxOS 发布物料清单门禁与发布辅助工具

以 tools/release-engineering/manifest.yaml 为唯一事实源（SSoT），做三件事：

  1. --check     物料清单门禁：校验清单 schema，并把清单中每条 active 物料的
                 evidence（产物 glob）与 .github/workflows/release.yml 的
                 「上传制品 + 发布附件」声明集合做双向比对——正向保证清单登记
                 的物料都被真实产出，反向保证流水线不产出未登记的物料。
  2. --checksums 按 09-release-process.md §3.2 的确定性规则生成 sha256sum 校验
                 和文本（按文件名排序，每行 `<sha256>  <文件名>`）。
  3. --notes     按 §3.7 渲染精简 release notes（概述 / 说明 / 其他 三节，
                 总文字不超过 12 行）；「说明」「其他」两节的内容由清单 notes
                 段提供，杜绝自动派生出的冗长章节。
  4. --channel   按 190-distribution/04-update-mechanism.md §2.3 生成发行
                 通道清单 channel.json（schema_version=1）。生成前做身份链
                 交叉断言：ISO 实测摘要与 iso-oci.txt 账本一致、构建号与
                 airymaxos-release NEVRA 的 Release 一致；任一不等即不产出。
  5. --replay    按清单登记的 produce（生成命令 argv）重放某条 active 物料
                 的生成过程，重放后逐条验证 evidence 有产物落地。清单只记
                 「结果」时无法复跑，故每条 active 物料必须登记 produce；
                 --check 同时断言命令引用的门禁真实存在，杜绝记录漂移。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；只做「清单 ↔ 发布流水线/仓库」的交叉比对，不重复实现
    流水线逻辑，也不在清单里重复登记 release.yml 已自校验的 tag 形态等约束。
  * 严禁桩实现：所有检查均读取真实文件与真实 git 历史，给出可复现的判定与证据。

权威依据：
  docs/docs-linux/120-development-process/09-release-process.md
    §3.1 物料清单总览（12 项）/ §3.2 源码 tarball 与校验和 / §3.7 release notes
  docs/docs-linux/70-build-system/03-ci-cd-pipeline.md（SDK tarball / OCI 镜像）
  docs/docs-linux/190-distribution/04-update-mechanism.md §2.3（发行通道清单）
  docs/docs-linux/190-distribution/06-sbom-generation.md（SBOM 格式）

Usage:
    python3 tools/release-engineering/release_manifest.py [options]
    python3 tools/release-engineering/release_manifest.py --checksums
        [options] FILE...
    python3 tools/release-engineering/release_manifest.py --notes [options]
    python3 tools/release-engineering/release_manifest.py --channel
        --version VER [options]
    python3 tools/release-engineering/release_manifest.py --replay ID
        --version VER [--tag TAG] [--repo OWNER/NAME] [--dry-run] [options]

Options:
    --check               物料清单门禁的 CI 模式（精简输出，仅退出码）
    --checksums           生成 sha256sum 校验和文本
    --notes               生成精简 release notes（概述 / 说明 / 其他）
    --channel             生成发行通道清单 channel.json
    --replay ID           按 produce 记录重放指定 active 物料的生成命令
    --dry-run             只打印展开后的命令，不执行（与 --replay 同用）
    --tag TAG             --replay 的 ${tag} 取值（默认 v<version>）
    --repo OWNER/NAME     --replay 的 ${repo} 取值（默认 openairymax/agent-linux）
    --repo-root PATH      agent-linux 仓库根（默认：自动探测）
    --manifest PATH       物料清单（默认：tools/release-engineering/manifest.yaml）
    --output PATH         结果写入文件（默认：标准输出）
    --version VER         release notes / channel.json 的版本号（必填）
    --iso PATH            --channel 的 ISO 文件（默认：与账本同名的同目录文件）
    --iso-oci PATH        iso-oci.txt 账本（默认：desktop/iso/iso-oci.txt）
    --kernel-rpm PATH     内核 RPM（默认：dist/rpm/x86_64 下唯一一份）
    --release-rpm PATH    airymaxos-release RPM（默认：dist/rpm 下唯一一份）
    --build N             期望构建号；给出时须与 airymaxos-release NEVRA 相等
    --notes-url URL       发行说明地址（默认：GitHub Release tag 页）
    --released-at STAMP   UTC 时间戳（默认：SOURCE_DATE_EPOCH，否则当前时间）

Exit codes:
    0 — 检查通过 / 生成成功
    1 — 一项或多项检查失败（含 --channel 交叉断言不等）
    2 — 用法/环境错误（清单缺失、YAML 非法、文件不可读、git 不可用等）

Example:
    >>> main(["--check"])
    0

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.3.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

__all__ = [
    "ChannelMismatch",
    "CheckResult",
    "Nevra",
    "ToolError",
    "build_channel",
    "build_checks",
    "build_parser",
    "build_sums",
    "check_pipeline",
    "check_recipes",
    "check_schema",
    "expand_argv",
    "find_material",
    "find_repo_root",
    "gate_names",
    "iso_asset",
    "load_yaml",
    "main",
    "normalize_glob",
    "parse_nevra",
    "ph_values",
    "pick_rpm",
    "pipeline_globs",
    "print_report",
    "read_ledger",
    "render_notes",
    "replay",
    "resolve_paths",
    "sha256_of",
    "stamp_of",
]

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/release-engineering/manifest.yaml"
RELEASE_WF_REL = ".github/workflows/release.yml"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

DELIVERY_ACTIVE = "active"
DELIVERY_DEFERRED = "deferred"
DELIVERY_CHOICES = (DELIVERY_ACTIVE, DELIVERY_DEFERRED)

CHANNEL_SCHEMA = 1
CHANNEL_NAME = "stable"
CHANNEL_DOMAIN = "https://github.com/openairymax/agent-linux/"
CHANNEL_URL = CHANNEL_DOMAIN + "releases/tag/v{ver}"
ISO_LEDGER_REL = "desktop/iso/iso-oci.txt"
KERNEL_RPM_GLOB = "dist/rpm/x86_64/airymaxos-kernel-[0-9]*.x86_64.rpm"
RELEASE_RPM_GLOB = "dist/rpm/**/airymaxos-release-*.noarch.rpm"

LEDGER_KEYS = ("name", "sha256", "size")
RPM_SUFFIX = ".rpm"
NEVRA_RE = re.compile(r"^(?P<name>.+?)-(?P<version>[^-]+)-(?P<release>[^-]+)"
                      r"\.(?P<arch>[^-]+)$")
RELEASE_NUM_RE = re.compile(r"^(?P<num>[0-9]+)(?P<dist>\.[0-9A-Za-z._]+)?$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
STAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"
                      r"T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

TOP_KEYS = frozenset({"version", "materials", "notes"})
MATERIAL_KEYS = frozenset({
    "id", "name", "format", "signature", "authority", "delivery",
    "evidence", "produce", "defer_reason", "defer_target",
})
REQUIRED_MATERIAL_KEYS = ("id", "name", "format", "signature",
                          "authority", "delivery")
NOTES_KEYS = frozenset({"summary", "remarks"})
NOTES_MAX_LINES = 3

DISPATCH_REL = "ci/ci.sh"
GATE_DIR_REL = "ci/gates"
PH_RE = re.compile(r"\$\{([a-z_]+)\}")
PRODUCE_PH = frozenset({"version", "tag", "repo"})
DEFAULT_REPO = "openairymax/agent-linux"
DEFAULT_TAG_FMT = "v{ver}"

UPLOAD_ARTIFACT_USES = "actions/upload-artifact"
GH_RELEASE_USES = "softprops/action-gh-release"
TEMPLATE_RE = re.compile(r"\$\{\{[^}]*\}\}")

NOTES_EMPTY = "（无）"

# ─── Types ─────────────────────────────────────────────────────────────────

Manifest = dict[str, Any]
Globs = set[str]

# ─── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    """单项门禁检查结果。

    Attributes:
        name: 检查项名称。
        passed: 是否通过。
        details: 失败详情（多行文本）。
    """

    name: str
    passed: bool
    details: str = ""


class ToolError(Exception):
    """输入缺失、文件不可读或 YAML 非法等致命错误。"""


# ─── Loading helpers ───────────────────────────────────────────────────────


def find_repo_root() -> Path:
    """从脚本位置自动探测 agent-linux 仓库根。

    脚本位于 <repo_root>/tools/release-engineering/release_manifest.py。

    Returns:
        仓库根路径。
    """
    return Path(__file__).resolve().parent.parent.parent


def load_yaml(path: Path, label: str) -> Manifest:
    """读取并解析一个 YAML 文件。

    Args:
        path: 文件路径。
        label: 文件用途标签（用于错误信息）。

    Returns:
        解析后的顶层映射。

    Raises:
        ToolError: PyYAML 缺失、文件不可读、YAML 非法或顶层不是映射。
    """
    _need_yaml()
    text = _read_yaml(path, label)
    return _parse_yaml(text, path, label)


def _need_yaml() -> None:
    """PyYAML 是解析清单的前提，缺失时给出安装提示。"""
    if yaml is None:
        raise ToolError("PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")


def _read_yaml(path: Path, label: str) -> str:
    """读取 YAML 文本，区分不存在与不可读两种失败。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ToolError(f"{label}文件不存在：{path}") from exc
    except OSError as exc:
        raise ToolError(f"{label}文件无法读取：{path}: {exc}") from exc


def _parse_yaml(text: str, path: Path, label: str) -> Manifest:
    """解析 YAML 并要求顶层是映射。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ToolError(f"{label} YAML 解析失败：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"{label}顶层必须是映射（mapping）：{path}")
    return data


def normalize_glob(raw: Any) -> str:
    """把流水线声明的产物路径规整为 glob。

    Args:
        raw: 流水线中的原始路径声明。

    Returns:
        模板表达式替换为 `*` 通配后的 glob。
    """
    return TEMPLATE_RE.sub("*", str(raw)).strip()


def _split_globs(value: Any) -> Globs:
    """把 upload-artifact/download 的块标量拆成规范化 glob 集合。"""
    if not isinstance(value, str):
        return set()
    return {normalize_glob(line) for line in value.splitlines()
            if normalize_glob(line)}


def _step_globs(step: Any) -> Globs:
    """按 step 的 uses 取它声明的产物 glob；非产出 step 返回空集。"""
    if not isinstance(step, dict):
        return set()
    params = _params(step)
    uses = str(step.get("uses", ""))
    return _uses_globs(uses, params)


def _params(step: dict) -> dict:
    """with 参数表；未声明时按空表处理。"""
    return step.get("with") or {}


def _uses_globs(uses: str, params: dict) -> Globs:
    """按 action 类型读取各自的产物路径键。"""
    if uses.startswith(UPLOAD_ARTIFACT_USES):
        return _split_globs(params.get("path"))
    if uses.startswith(GH_RELEASE_USES):
        return _split_globs(params.get("files"))
    return set()


def _job_globs(job: Any) -> Globs:
    """单个 job 内全部产出 step 的 glob 并集。"""
    if not isinstance(job, dict):
        return set()
    globs: Globs = set()
    for step in job.get("steps") or []:
        globs |= _step_globs(step)
    return globs


def pipeline_globs(path: Path) -> Globs:
    """收集发布流水线声明的全部产物 glob。

    只登记 `actions/upload-artifact` 的 `with.path` 与
    `softprops/action-gh-release` 的 `with.files`——即流水线对外产出的物料。

    Args:
        path: release.yml 路径。

    Returns:
        规范化后的产物 glob 集合。

    Raises:
        ToolError: 流水线不可读、缺少 jobs 或未声明任何产物。
    """
    doc = load_yaml(path, "发布流水线")
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        raise ToolError(f"发布流水线缺少 jobs：{path}")

    globs: Globs = set()
    for job in jobs.values():
        globs |= _job_globs(job)
    if not globs:
        raise ToolError(f"发布流水线未声明任何产物（upload-artifact / release）："
                        f"{path}")
    return globs


# ─── Schema check ──────────────────────────────────────────────────────────


def _entry_fields(where: str, entry: Manifest) -> list[str]:
    """字段集合：未登记键与缺值必填键，两者都按名逐个报出。"""
    problems = []
    unknown = set(entry) - MATERIAL_KEYS
    if unknown:
        problems.append(f"{where} 未知字段: {', '.join(sorted(unknown))}")
    problems.extend(f"{where} 缺少或为空值的必填字段: {key}"
                    for key in REQUIRED_MATERIAL_KEYS if not entry.get(key))
    return problems


def _entry_id(where: str, entry: Manifest, seen: set[str]) -> list[str]:
    """物料 id 必须存在且唯一；首次出现即登记，重复出现报错。"""
    mid = entry.get("id")
    if not isinstance(mid, str) or not mid:
        return []
    if mid in seen:
        return [f"{where}.id 重复: {mid}"]
    seen.add(mid)
    return []


def _entry_delivery(where: str, entry: Manifest) -> list[str]:
    """交付状态：取值须在枚举内，且与该状态要求的字段组合自洽。"""
    delivery = entry.get("delivery")
    problems = []
    if delivery is not None and delivery not in DELIVERY_CHOICES:
        problems.append(f"{where}.delivery 必须是 {' 或 '.join(DELIVERY_CHOICES)}")
    problems.extend(_delivery_errs(where, entry, delivery))
    return problems


def _entry_problems(idx: int, entry: Any, seen: set[str]) -> list[str]:
    """校验单条 materials 记录。

    Args:
        idx: 记录下标（用于错误信息）。
        entry: 记录内容。
        seen: 已出现的物料 id 集合（就地更新）。

    Returns:
        问题描述列表；空列表表示通过。
    """
    where = f"materials[{idx}]"
    if not isinstance(entry, dict):
        return [f"{where} 必须是映射"]
    return (_entry_fields(where, entry)
            + _entry_id(where, entry, seen)
            + _entry_delivery(where, entry))


def _delivery_errs(where: str, entry: Manifest, delivery: Any) -> list[str]:
    """按 delivery 状态校验 evidence / produce / defer_* 的组合合法性。"""
    checks = {DELIVERY_ACTIVE: _active_errs,
              DELIVERY_DEFERRED: _deferred_errs}
    check = checks.get(delivery)
    return [] if check is None else check(where, entry)


def _evidence_errs(where: str, evidence: Any) -> list[str]:
    """active 物料的 evidence 必须是非空列表，且每项为非空字符串。"""
    if not _nonempty_list(evidence):
        return [f"{where}: active 物料要求非空 evidence 列表"]
    if _blank_glob(evidence):
        return [f"{where}.evidence 含非法项"]
    return []


def _nonempty_list(value: Any) -> bool:
    """值必须是非空列表。"""
    return isinstance(value, list) and bool(value)


def _blank_glob(evidence: list) -> bool:
    """任一 evidence 项不是非空字符串即为非法。"""
    return any(not isinstance(g, str) or not g for g in evidence)


def _defer_errs(where: str, entry: Manifest) -> list[str]:
    """active 物料不得登记 defer_reason / defer_target。"""
    if entry.get("defer_reason") or entry.get("defer_target"):
        return [f"{where}: active 物料不得登记 defer_reason/defer_target"]
    return []


def _active_errs(where: str, entry: Manifest) -> list[str]:
    """active 物料：evidence 与 produce 都必须可复跑，且不得登记延期字段。"""
    return (_evidence_errs(where, entry.get("evidence"))
            + _produce_errs(where, entry.get("produce"))
            + _defer_errs(where, entry))


def _deferred_errs(where: str, entry: Manifest) -> list[str]:
    """deferred 物料：延期理由与目标必须齐备，且不得留生成痕迹。"""
    problems = [f"{where}: deferred 物料要求 {key}"
                for key in ("defer_reason", "defer_target")
                if not entry.get(key)]
    problems.extend(_defer_left(where, entry))
    return problems


def _defer_left(where: str, entry: Manifest) -> list[str]:
    """不得登记 evidence 或 produce 的残留痕迹。"""
    problems = []
    if entry.get("evidence"):
        problems.append(f"{where}: deferred 物料不得登记 evidence")
    if entry.get("produce"):
        problems.append(f"{where}: deferred 物料不得登记 produce")
    return problems


def _produce_errs(where: str, produce: Any) -> list[str]:
    """校验 active 物料的生成命令记录形状。

    produce 是「每条物料记录生成命令 argv」的落点：外层按执行顺序排列，
    每项为一条 argv（token 列表），缺失或空列表即视为不可复跑。
    """
    if not isinstance(produce, list) or not produce:
        return [f"{where}: active 物料要求非空 produce 列表（生成命令 argv）"]
    problems = []
    for idx, argv in enumerate(produce):
        problems.extend(_argv_errs(f"{where}.produce[{idx}]", argv))
    return problems


def _argv_errs(where: str, argv: Any) -> list[str]:
    """单条 argv 校验：须为非空列表，且每个 token 均为非空字符串。"""
    if not _nonempty_list(argv):
        return [f"{where} 必须是非空 argv 列表"]
    if _blank_token(argv):
        return [f"{where} 含非字符串或空 token"]
    return []


def _blank_token(argv: list) -> bool:
    """任一 token 不是去空白后非空的字符串即为非法。"""
    return any(not isinstance(tok, str) or not tok.strip() for tok in argv)


def _notes_unknown(notes: dict) -> list[str]:
    """notes 段只登记 NOTES_KEYS，多出的键即为拼写或扩张漂移。"""
    unknown = set(notes) - NOTES_KEYS
    return [f"notes 未知字段: {', '.join(sorted(unknown))}"] if unknown else []


def _note_errs(key: str, value: Any) -> list[str]:
    """单键校验：须为条目非空的字符串列表，且条数不超过上限。"""
    if not _note_list(value):
        return [f"notes.{key} 必须是字符串列表且条目非空"
                "（确无此类条目时用空列表）"]
    if len(value) > NOTES_MAX_LINES:
        return [f"notes.{key} 至多 {NOTES_MAX_LINES} 条（当前 {len(value)} 条）"]
    return []


def _note_list(value: Any) -> bool:
    """值必须是逐条去空白后非空的字符串列表。"""
    return isinstance(value, list) and not any(
        not isinstance(item, str) or not item.strip() for item in value)


def _notes_errs(notes: Any) -> list[str]:
    """校验清单 notes 披露段。

    notes 是 release notes「说明」「其他」两章节的唯一事实源，两键均须显式
    声明为不超过 NOTES_MAX_LINES 条的字符串列表；确无此类条目时用空列表
    表达，不得缺键，以杜绝「章节静默为空」与「发行说明冗长」两类漂移。

    Args:
        notes: 清单 notes 段（可能缺失）。

    Returns:
        问题描述列表；空列表表示通过。
    """
    if notes is None:
        return ["缺少必填字段: notes（release notes 披露段）"]
    if not isinstance(notes, dict):
        return ["notes 必须是映射"]

    problems = _notes_unknown(notes)
    for key in sorted(NOTES_KEYS):
        problems.extend(_note_errs(key, notes.get(key)))
    return problems


def check_schema(manifest: Manifest) -> CheckResult:
    """校验清单 schema。

    覆盖顶层字段、必填字段、id 唯一、delivery 取值与配套字段。

    Args:
        manifest: 清单顶层映射。

    Returns:
        检查结果。
    """
    problems = _top_probs(manifest)
    problems.extend(_notes_errs(manifest.get("notes")))
    problems.extend(_mat_probs(manifest.get("materials")))
    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


def _top_probs(manifest: Manifest) -> list[str]:
    """顶层未知字段与 version 必填性。"""
    problems = []
    unknown = set(manifest) - TOP_KEYS
    if unknown:
        problems.append(f"顶层未知字段: {', '.join(sorted(unknown))}")
    if not manifest.get("version"):
        problems.append("缺少或为空值的必填字段: version")
    return problems


def _mat_probs(materials: Any) -> list[str]:
    """materials 非空性，以及逐条物料记录的问题。"""
    if not isinstance(materials, list) or not materials:
        return ["materials 必须是非空列表"]
    seen: set[str] = set()
    problems = []
    for idx, entry in enumerate(materials):
        problems.extend(_entry_problems(idx, entry, seen))
    return problems


# ─── Recipe (produce) check ────────────────────────────────────────────────


def gate_names(repo_root: Path) -> set[str]:
    """返回仓库内真实存在的门禁名（ci/gates/*.sh 去后缀）。"""
    return {path.stem for path in (repo_root / GATE_DIR_REL).glob("*.sh")}


def _recipe_errs(entry: Manifest, gates: set[str]) -> list[str]:
    """校验单条 active 物料的 produce 是否指向真实门禁且占位符可解析。"""
    mid = entry.get("id")
    problems = []
    for idx, argv in enumerate(entry.get("produce") or []):
        problems.extend(_recipe_one(f"物料 {mid} 的 produce[{idx}]", argv, gates))
    return problems


def _recipe_one(where: str, argv: Sequence[str], gates: set[str]) -> list[str]:
    """单条 produce argv 校验；命令头不对时不再解读其余 token。"""
    if len(argv) < 2 or argv[0] != DISPATCH_REL:
        return [f"{where} 必须以 {DISPATCH_REL} <门禁> 开头"]
    problems = _need_gate(where, argv[1], gates)
    problems += _ph_probs(where, argv)
    return problems


def _need_gate(where: str, gate: str, gates: set[str]) -> list[str]:
    """命令头之后必须指向仓库内真实门禁。"""
    if gate in gates:
        return []
    return [f"{where} 引用的门禁不存在: {gate}"]


def _ph_probs(where: str, argv: Sequence[str]) -> list[str]:
    """占位符限于可解析白名单，未知的排序后一次列出。"""
    unknown = {m for tok in argv for m in PH_RE.findall(tok)} - PRODUCE_PH
    if not unknown:
        return []
    return [f"{where} 含未知占位符: {', '.join(sorted(unknown))}"]


def check_recipes(manifest: Manifest, repo_root: Path) -> CheckResult:
    """校验每条 active 物料的生成命令记录可复算。

    清单只记结果不记命令时，产物无法从清单复跑；本项要求 produce 指向
    仓库内真实门禁，且占位符限于可解析的白名单，防止记录随流水线漂移。

    Args:
        manifest: 清单顶层映射。
        repo_root: 仓库根路径。

    Returns:
        检查结果。
    """
    gates = gate_names(repo_root)
    problems = []
    for entry in manifest.get("materials", []):
        if entry.get("delivery") != DELIVERY_ACTIVE:
            continue
        problems.extend(_recipe_errs(entry, gates))
    return CheckResult("物料生成命令可复算", not problems, "\n".join(problems))


# ─── Pipeline consistency check ────────────────────────────────────────────


def check_pipeline(manifest: Manifest, repo_root: Path) -> CheckResult:
    """校验物料清单与发布流水线产物双向一致。

    Args:
        manifest: 清单顶层映射。
        repo_root: 仓库根路径。

    Returns:
        检查结果。
    """
    name = "物料清单 ↔ 发布流水线双向一致"
    path = repo_root / RELEASE_WF_REL
    try:
        declared = pipeline_globs(path)
    except ToolError as exc:
        return CheckResult(name, False, str(exc))

    problems, evidenced = _declared_probs(manifest, declared)
    problems.extend(_unlisted(declared, evidenced))
    return CheckResult(name, not problems, "\n".join(problems))


def _declared_probs(manifest: Manifest, declared: Globs) -> tuple[list[str], Globs]:
    """active 物料声明的产物必须在流水线里声明，返回问题与取证集合。"""
    problems: list[str] = []
    evidenced: Globs = set()
    for item in manifest.get("materials", []):
        if item.get("delivery") != DELIVERY_ACTIVE:
            continue
        problems.extend(_item_probs(item, declared, evidenced))
    return problems, evidenced


def _item_probs(item: Manifest, declared: Globs, evidenced: Globs) -> list[str]:
    """单条物料登记的全部产物：逐个归一化、记账并要求流水线已声明。"""
    problems = []
    for raw in item.get("evidence", []):
        glob = normalize_glob(raw)
        evidenced.add(glob)
        if glob not in declared:
            problems.append(
                f"active 物料 {item.get('id')} 的产物 {glob} 未在 release.yml 声明")
    return problems


def _unlisted(declared: Globs, evidenced: Globs) -> list[str]:
    """流水线产出而清单未登记，按字节序逐条列出。"""
    return [f"release.yml 产出 {glob} 未在清单登记（未登记物料）"
            for glob in sorted(declared - evidenced)]


# ─── Checksums ─────────────────────────────────────────────────────────────


def sha256_of(path: str | Path) -> str:
    """流式计算文件 sha256 摘要。

    Args:
        path: 待校验文件路径。

    Returns:
        十六进制 sha256 摘要。
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_sums(files: Iterable[str | Path]) -> str:
    """按文件名排序生成 sha256sum 文本。

    Args:
        files: 待校验文件路径集合。

    Returns:
        每行 `<sha256>  <文件名>` 的确定性文本。

    Raises:
        ToolError: 文件不存在或未提供任何文件。
    """
    entries = []
    for raw in sorted(files, key=lambda item: Path(item).name):
        path = Path(raw)
        if not path.is_file():
            raise ToolError(f"待校验文件不存在：{raw}")
        entries.append(f"{sha256_of(path)}  {path.name}")
    if not entries:
        raise ToolError("未提供任何待校验文件")
    return "\n".join(entries) + "\n"


# ─── Release notes ─────────────────────────────────────────────────────────


def _bullet_list(items: Sequence[str]) -> str:
    """把条目渲染为 Markdown 列表；空则返回占位符。"""
    if not items:
        return NOTES_EMPTY
    return "\n".join(f"- {item}" for item in items)


def render_notes(version: str, notes: Manifest | None = None) -> str:
    """按 §3.7 渲染精简 release notes Markdown（概述 / 说明 / 其他）。

    Args:
        version: 版本号。
        notes: 清单 notes 披露段（「说明」/「其他」两节的唯一事实源）。

    Returns:
        完整的 release notes Markdown；三节合计文字不超过 12 行。
    """
    sections = _note_sections(version, notes or {})
    body = "\n\n".join(
        f"## {title}\n{content}" for title, content in sections)
    return f"{body}\n"


def _note_sections(version: str,
                   disclosures: Manifest) -> list[tuple[str, str]]:
    """概述 / 说明 / 其他三节。"""
    return [
        ("概述", f"- 版本号 v{version}\n- 发布日期 {date.today():%Y%m}"),
        ("说明", _bullet_list(disclosures.get("summary") or [])),
        ("其他", _bullet_list(disclosures.get("remarks") or [])),
    ]


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(
    results: Sequence[CheckResult],
    manifest: Manifest,
    check_mode: bool,
) -> None:
    """打印门禁报告。

    Args:
        results: 各检查项结果。
        manifest: 清单顶层映射（用于报告头部）。
        check_mode: True 时输出 CI 精简格式。
    """
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    failed = total - passed

    if check_mode:
        _report_ci(results, passed, total, failed)
        return
    _report_full(results, manifest, total, failed)


def _report_ci(results: Sequence[CheckResult], passed: int, total: int,
               failed: int) -> None:
    """CI 精简形态：一行判定，失败项逐条附明细。"""
    if failed == 0:
        print(f"RELEASE-MANIFEST: PASS ({passed}/{total} checks)")
        return
    print(f"RELEASE-MANIFEST: FAIL ({passed}/{total} checks, "
          f"{failed} failed)")
    _ci_fails(results)


def _ci_fails(results: Sequence[CheckResult]) -> None:
    """逐条打印失败项及其明细。"""
    for result in results:
        if not result.passed:
            print(f"  FAIL: {result.name}")
            _ci_lines(result)


def _ci_lines(result: CheckResult) -> None:
    """失败项明细缩进两格。"""
    for line in result.details.splitlines():
        print(f"    {line}")


def _report_full(results: Sequence[CheckResult], manifest: Manifest,
                 total: int, failed: int) -> None:
    """详细形态：抬头、逐项判定与结论横幅。"""
    _full_head(manifest)
    for result in results:
        _full_item(result)
    _full_tail(total, failed)


def _full_head(manifest: Manifest) -> None:
    """详细形态抬头：清单版本与物料数。"""
    print("=" * 72)
    print("agent-linux AirymaxOS 发布物料清单报告")
    print(f"清单版本: {manifest.get('version', '?')}    "
          f"物料数: {len(manifest.get('materials', []))}")
    print("=" * 72)
    print()


def _full_item(result: CheckResult) -> None:
    """单条检查的判定行与明细。"""
    marker = "[+]" if result.passed else "[-]"
    status = "PASS" if result.passed else "FAIL"
    print(f"{marker} {status}: {result.name}")
    for line in result.details.splitlines():
        print(f"      {line}")


def _full_tail(total: int, failed: int) -> None:
    """详细形态结论横幅。"""
    print()
    print("-" * 72)
    if failed == 0:
        print(f"RESULT: PASS — 全部 {total} 项检查通过")
    else:
        print(f"RESULT: FAIL — {failed} 项检查未通过")
    print("-" * 72)


# ─── Modes ─────────────────────────────────────────────────────────────────


def build_checks(manifest: Manifest, repo_root: Path) -> list[CheckResult]:
    """构建门禁检查序列：schema 失败即短路。

    Args:
        manifest: 清单顶层映射。
        repo_root: 仓库根路径。

    Returns:
        检查结果列表。
    """
    schema = check_schema(manifest)
    if not schema.passed:
        return [schema]
    return [schema, check_recipes(manifest, repo_root),
            check_pipeline(manifest, repo_root)]


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """定位仓库根与物料清单路径（显式 --manifest 优先）。

    Args:
        args: 命令行参数。

    Returns:
        `(仓库根, 清单路径)`。

    Raises:
        ToolError: 仓库根不存在。
    """
    repo_root = Path(args.repo_root) if args.repo_root else find_repo_root()
    if not repo_root.is_dir():
        raise ToolError(f"仓库根不存在：{repo_root}")
    manifest_path = (Path(args.manifest) if args.manifest
                     else repo_root / DEFAULT_MANIFEST_REL)
    return repo_root, manifest_path


def emit(text: str, output: str | None) -> None:
    """把结果写往文件或标准输出。

    Args:
        text: 待写出文本。
        output: 目标文件路径；None 表示标准输出。

    Raises:
        ToolError: 目标文件不可写。
    """
    if output:
        try:
            Path(output).write_text(text, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"结果无法写入 {output}: {exc}") from exc
        print(f"已写入 {output}")
    else:
        sys.stdout.write(text)


def cmd_check(args: argparse.Namespace) -> int:
    """运行物料清单门禁。"""
    repo_root, manifest_path = resolve_paths(args)
    manifest = load_yaml(manifest_path, "物料清单")
    results = build_checks(manifest, repo_root)
    print_report(results, manifest, args.check)
    failed = sum(1 for result in results if not result.passed)
    return EXIT_FAIL if failed else EXIT_PASS


def cmd_checksums(args: argparse.Namespace) -> int:
    """生成确定性 sha256sum 校验和文本。"""
    emit(build_sums(args.files), args.output)
    return EXIT_PASS


def cmd_notes(args: argparse.Namespace) -> int:
    """生成精简 release notes。"""
    if not args.version:
        print("ERROR: --notes 需要 --version 指定版本号", file=sys.stderr)
        return EXIT_USAGE
    try:
        _, manifest_path = resolve_paths(args)
        manifest = load_yaml(manifest_path, "物料清单")
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    notes = manifest.get("notes")
    if not isinstance(notes, dict):
        print("ERROR: 物料清单缺少 notes 披露段："
              "「说明」/「其他」须以 SSoT 声明（§3.7）", file=sys.stderr)
        return EXIT_USAGE
    emit(render_notes(args.version, notes), args.output)
    return EXIT_PASS


# ─── Recipe replay ─────────────────────────────────────────────────────────


def find_material(manifest: Manifest, mid: str) -> Manifest:
    """按 id 取出物料条目。

    Args:
        manifest: 清单顶层映射。
        mid: 物料 id。

    Returns:
        物料条目。

    Raises:
        ToolError: id 未登记。
    """
    for entry in manifest.get("materials", []):
        if entry.get("id") == mid:
            return entry
    raise ToolError(f"物料未登记: {mid}")


def ph_values(args: argparse.Namespace) -> dict[str, str]:
    """从命令行参数导出 produce 占位符取值。

    Args:
        args: 命令行参数（version / tag / repo 可为空）。

    Returns:
        占位符名到取值的映射；未给出的取值留空，由 expand_argv 报错。
    """
    ver = args.version or ""
    return {
        "version": ver,
        "tag": args.tag or _tag_of(ver),
        "repo": args.repo or DEFAULT_REPO,
    }


def _tag_of(ver: str) -> str:
    """缺省标签按版本号推导；版本为空则留空待报错。"""
    return DEFAULT_TAG_FMT.format(ver=ver) if ver else ""


def expand_argv(argv: Sequence[str], values: dict[str, str]) -> list[str]:
    """展开一条 argv 中的占位符。

    Args:
        argv: 命令 token 列表。
        values: 占位符取值。

    Returns:
        展开后的 token 列表。

    Raises:
        ToolError: 占位符未知或取值为空。
    """
    expanded = []
    for tok in argv:
        _ph_check(tok, values)
        expanded.append(PH_RE.sub(lambda m: values[m.group(1)], tok))
    return expanded


def _ph_check(tok: str, values: dict[str, str]) -> None:
    """token 内占位符必须已知且有取值。"""
    for name in PH_RE.findall(tok):
        if name not in values:
            raise ToolError(f"未知占位符: ${{{name}}}")
        if not values[name]:
            raise ToolError(
                f"占位符 ${{{name}}} 无取值（用 --version/--tag/--repo 指定）")


def _ev_missing(entry: Manifest, repo_root: Path) -> list[str]:
    """返回重放后仍无产物落地的 evidence glob。"""
    return [glob for glob in entry.get("evidence", [])
            if not any(repo_root.glob(glob))]


def _run_argv(cmd: list[str], repo_root: Path) -> int:
    """执行一条生成命令，返回其退出码。"""
    try:
        return subprocess.run(cmd, cwd=repo_root, check=False).returncode
    except OSError as exc:
        raise ToolError(f"生成命令无法执行: {' '.join(cmd)}（{exc}）") from exc


def _replay_head(entry: Manifest, mid: str) -> int | None:
    """重放前置：物料须为 active 且登记了生成命令，否则返回用法错误码。"""
    if entry.get("delivery") != DELIVERY_ACTIVE:
        print(f"ERROR: 物料 {mid} 未启用（deferred），无生成命令可重放",
              file=sys.stderr)
        return EXIT_USAGE
    if not entry.get("produce"):
        print(f"ERROR: 物料 {mid} 未登记生成命令（produce）", file=sys.stderr)
        return EXIT_USAGE
    return None


def _replay_cmds(entry: Manifest, repo_root: Path, values: dict[str, str],
                 dry_run: bool) -> int:
    """按序重放生成命令；任一命令非零退出即中止并返回失败码。"""
    for argv in entry.get("produce") or []:
        if _replay_one(argv, repo_root, values, dry_run):
            return EXIT_FAIL
    return EXIT_PASS


def _replay_one(argv: Sequence[str], repo_root: Path,
                values: dict[str, str], dry_run: bool) -> bool:
    """展开并执行一条生成命令，返回是否失败。"""
    cmd = expand_argv(argv, values)
    print("+ " + " ".join(cmd))
    if dry_run:
        return False
    code = _run_argv(cmd, repo_root)
    if not code:
        return False
    print(f"ERROR: 生成命令失败（退出码 {code}）: {' '.join(cmd)}",
          file=sys.stderr)
    return True


def _replay_ev(entry: Manifest, repo_root: Path, mid: str) -> int:
    """重放后逐条验证 evidence 有产物落地。"""
    missing = _ev_missing(entry, repo_root)
    if missing:
        print(f"ERROR: 物料 {mid} 重放后无产物: {', '.join(missing)}",
              file=sys.stderr)
        return EXIT_FAIL
    print(f"REPLAY: PASS {mid}")
    return EXIT_PASS


def replay(manifest: Manifest, repo_root: Path, mid: str,
           values: dict[str, str], dry_run: bool) -> int:
    """按 produce 记录重放某条物料的生成过程。

    Args:
        manifest: 清单顶层映射。
        repo_root: 仓库根路径（命令工作目录）。
        mid: 物料 id。
        values: 占位符取值。
        dry_run: 只打印展开后的命令，不执行。

    Returns:
        退出码：0 重放成功且 evidence 有产物；1 命令失败或产物缺失；
        2 用法错误。
    """
    entry = find_material(manifest, mid)
    head = _replay_head(entry, mid)
    if head is not None:
        return head
    code = _replay_cmds(entry, repo_root, values, dry_run)
    if code or dry_run:
        return code
    return _replay_ev(entry, repo_root, mid)


def cmd_replay(args: argparse.Namespace) -> int:
    """运行生成命令重放。"""
    try:
        repo_root, manifest_path = resolve_paths(args)
        manifest = load_yaml(manifest_path, "物料清单")
        return replay(manifest, repo_root, args.replay,
                      ph_values(args), args.dry_run)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE


# ─── Channel manifest ──────────────────────────────────────────────────────


class ChannelMismatch(Exception):
    """身份链交叉断言不符：不产出 channel.json，发布判失败。"""


@dataclass(frozen=True)
class Nevra:
    """RPM 文件名解析出的 NEVRA 四段。

    Attributes:
        name: 包名。
        version: 版本号（AirymaxOS 取 `%{airy_version}`）。
        release: 发布号（AirymaxOS 取 `%{airy_release}`，可能带 `.dist` 后缀）。
        arch: 架构。
    """

    name: str
    version: str
    release: str
    arch: str

    @property
    def build(self) -> int:
        """发布号的前导整数，即 04-update-mechanism §2.2 的 `build`。"""
        matched = RELEASE_NUM_RE.match(self.release)
        if not matched:
            raise ChannelMismatch(
                f"NEVRA Release 不是数值构建号：{self.text()}")
        return int(matched.group("num"))

    def text(self) -> str:
        """还原 `name-version-release.arch` 形态。"""
        return f"{self.name}-{self.version}-{self.release}.{self.arch}"


def parse_nevra(path: Path) -> Nevra:
    """从 RPM 文件名解析 NEVRA。

    Args:
        path: RPM 文件路径（只解析名字，不读取内容）。

    Returns:
        解析结果。

    Raises:
        ToolError: 后缀不是 .rpm 或名字不是 NEVRA 形态。
    """
    if not path.name.endswith(RPM_SUFFIX):
        raise ToolError(f"不是 RPM 文件：{path}")
    matched = NEVRA_RE.match(path.name[:-len(RPM_SUFFIX)])
    if not matched:
        raise ToolError(f"文件名不是 NEVRA 形态：{path.name}")
    return Nevra(**matched.groupdict())


def _ledger_fields(lines: Sequence[str]) -> dict[str, str]:
    """逐行取值：只认 LEDGER_KEYS，同键保留首次出现的值。"""
    ledger: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() in LEDGER_KEYS:
            ledger.setdefault(key.strip(), value.strip())
    return ledger


def _ledger_check(ledger: dict[str, str], path: Path) -> None:
    """字段齐备性与取值合法性；任一项不成立即抛 ToolError。"""
    _ledger_missing(ledger, path)
    _ledger_sha(ledger)
    _ledger_size(ledger)


def _ledger_missing(ledger: dict[str, str], path: Path) -> None:
    """账本必须携带全部身份字段。"""
    missing = [key for key in LEDGER_KEYS if key not in ledger]
    if missing:
        raise ToolError(f"ISO 账本缺少字段 {'/'.join(missing)}：{path}")


def _ledger_sha(ledger: dict[str, str]) -> None:
    """sha256 必须是完整的十六进制摘要。"""
    if not DIGEST_RE.match(ledger["sha256"]):
        raise ToolError(f"ISO 账本 sha256 非法：{ledger['sha256']}")


def _ledger_size(ledger: dict[str, str]) -> None:
    """size 必须是十进制字节数。"""
    if not ledger["size"].isdigit():
        raise ToolError(f"ISO 账本 size 非法：{ledger['size']}")


def read_ledger(path: Path) -> dict[str, str]:
    """读取 iso-oci.txt 账本的单值字段。

    `part=` 行携带逐块摘要，键名不同，按 LEDGER_KEYS 白名单自然跳过；
    同一键只取首次出现的值，避免后续行覆盖身份字段。

    Args:
        path: 账本路径。

    Returns:
        含 name / sha256 / size 的映射。

    Raises:
        ToolError: 账本不可读、缺字段或字段非法。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ToolError(f"ISO 账本不存在：{path}") from exc
    except OSError as exc:
        raise ToolError(f"ISO 账本无法读取：{path}: {exc}") from exc

    ledger = _ledger_fields(lines)
    _ledger_check(ledger, path)
    return ledger


def pick_rpm(repo_root: Path, pattern: str, label: str) -> Path:
    """按 glob 取出唯一一份 RPM。

    Args:
        repo_root: 仓库根路径。
        pattern: 相对仓库根的 glob。
        label: 物料标签（用于错误信息）。

    Returns:
        命中的 RPM 路径。

    Raises:
        ToolError: 命中数不为 1。
    """
    found = sorted(repo_root.glob(pattern))
    if len(found) != 1:
        raise ToolError(f"{label} 应恰好一份（{pattern}），实际 {len(found)} 份")
    return found[0]


def digest_of(path: Path, label: str) -> str:
    """计算文件摘要，文件缺失时报用法错误。"""
    if not path.is_file():
        raise ToolError(f"{label}不存在：{path}")
    return sha256_of(path)


def iso_asset(iso: Path, ledger: dict[str, str]) -> dict[str, object]:
    """以实测值构造 ISO 资产段，并与账本逐项交叉断言。

    Args:
        iso: ISO 文件路径。
        ledger: read_ledger() 的结果。

    Returns:
        `{"name", "size", "sha256"}`。

    Raises:
        ToolError: ISO 不存在。
        ChannelMismatch: 文件名、大小或摘要与账本任一不等。
    """
    if not iso.is_file():
        raise ToolError(f"ISO 不存在：{iso}")
    _need_names(iso, ledger)
    size = iso.stat().st_size
    _need_size(size, ledger)
    digest = sha256_of(iso)
    _need_digest(digest, ledger)
    return {"name": iso.name, "size": size, "sha256": digest}


def _need_names(iso: Path, ledger: dict[str, str]) -> None:
    """文件名必须与账本一致。"""
    if iso.name != ledger["name"]:
        raise ChannelMismatch(
            f"ISO 文件名与账本不一致：{iso.name} != {ledger['name']}")


def _need_size(size: int, ledger: dict[str, str]) -> None:
    """大小必须与账本一致。"""
    if size != int(ledger["size"]):
        raise ChannelMismatch(
            f"ISO 大小与账本不一致：{size} != {ledger['size']}")


def _need_digest(digest: str, ledger: dict[str, str]) -> None:
    """摘要必须与账本一致。"""
    if digest != ledger["sha256"]:
        raise ChannelMismatch(
            f"ISO 摘要与账本不一致：{digest} != {ledger['sha256']}")


def stamp_of(raw: str | None) -> str:
    """确定 released_at 时间戳。

    Args:
        raw: `--released-at` 的显式取值。

    Returns:
        `YYYY-MM-DDTHH:MM:SSZ` 形态的 UTC 时间戳。

    Raises:
        ToolError: 显式取值或 SOURCE_DATE_EPOCH 非法。
    """
    if raw:
        return _need_stamp(raw)
    return _epoch_stamp()


def _need_stamp(raw: str) -> str:
    """显式时间戳必须符合约定形态。"""
    if not STAMP_RE.match(raw):
        raise ToolError(f"--released-at 须为 {STAMP_FMT} 形态：{raw}")
    return raw


def _epoch_stamp() -> str:
    """SOURCE_DATE_EPOCH 优先，否则取当前 UTC 时间。"""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        return datetime.now(timezone.utc).strftime(STAMP_FMT)
    try:
        moment = datetime.fromtimestamp(int(epoch), timezone.utc)
    except ValueError as exc:
        raise ToolError(f"SOURCE_DATE_EPOCH 非法：{epoch}") from exc
    return moment.strftime(STAMP_FMT)


def _ledger_of(args: argparse.Namespace,
               repo_root: Path) -> tuple[Path, dict[str, Any]]:
    """账本路径与内容：显式 --iso-oci 优先，缺省按仓库约定推导。"""
    path = Path(args.iso_oci) if args.iso_oci else repo_root / ISO_LEDGER_REL
    return path, read_ledger(path)


def _rpms_of(args: argparse.Namespace, repo_root: Path) -> tuple[Path, Path]:
    """内核与发行包路径：显式参数优先，缺省各自按 glob 唯一匹配。"""
    kernel = (Path(args.kernel_rpm) if args.kernel_rpm
              else pick_rpm(repo_root, KERNEL_RPM_GLOB, "内核 RPM"))
    release = (Path(args.release_rpm) if args.release_rpm
               else pick_rpm(repo_root, RELEASE_RPM_GLOB,
                             "airymaxos-release RPM"))
    return kernel, release


def _check_identity(args: argparse.Namespace, kernel: Nevra,
                     release: Nevra) -> None:
    """版本号须与两枚 NEVRA 相符；给出 --build 时须与发行包 Release 相等。"""
    if args.version not in (kernel.version, release.version):
        raise ChannelMismatch(
            f"版本号与制品不一致：--version {args.version}，"
            f"内核 {kernel.version}，发行包 {release.version}")
    if args.build is not None and args.build != release.build:
        raise ChannelMismatch(
            f"构建号与发行包 NEVRA 不一致：--build {args.build} "
            f"!= {release.build}")


def _notes_url(args: argparse.Namespace) -> str:
    """发行说明地址须落在发行域，否则通道清单会把取件方引到第三方。"""
    url = args.notes_url or CHANNEL_URL.format(ver=args.version)
    if not url.startswith(CHANNEL_DOMAIN):
        raise ToolError(f"notes_url 须落在发行域 {CHANNEL_DOMAIN}：{url}")
    return url


def _chan_payload(args: argparse.Namespace, ledger: dict[str, Any],
                     iso: Path, kernel_path: Path,
                     kernel: Nevra, release: Nevra) -> dict[str, Any]:
    """按 04-update-mechanism.md §2.3 组装通道清单字段。"""
    return {
        "schema_version": CHANNEL_SCHEMA,
        "channel": CHANNEL_NAME,
        "version": args.version,
        "build": release.build,
        "released_at": stamp_of(args.released_at),
        "iso": iso_asset(iso, ledger),
        "kernel": {
            "nevra": kernel.text(),
            "sha256": digest_of(kernel_path, "内核 RPM"),
        },
        "notes_url": _notes_url(args),
    }


def build_channel(args: argparse.Namespace, repo_root: Path) -> str:
    """组装 channel.json，并在产出前完成两条身份链交叉断言。

    Args:
        args: 命令行参数。
        repo_root: 仓库根路径。

    Returns:
        格式化后的 JSON 文本。

    Raises:
        ToolError: 输入文件缺失或参数非法。
        ChannelMismatch: 版本、构建号、ISO 摘要与账本/NEVRA 任一不等。
    """
    ledger_path, ledger = _ledger_of(args, repo_root)
    iso = Path(args.iso) if args.iso else ledger_path.parent / ledger["name"]
    kernel_path, release_path = _rpms_of(args, repo_root)
    kernel = parse_nevra(kernel_path)
    release = parse_nevra(release_path)
    _check_identity(args, kernel, release)
    payload = _chan_payload(args, ledger, iso, kernel_path, kernel, release)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def cmd_channel(args: argparse.Namespace) -> int:
    """生成发行通道清单 channel.json。"""
    if not args.version:
        print("ERROR: --channel 需要 --version 指定版本号", file=sys.stderr)
        return EXIT_USAGE
    try:
        repo_root, _ = resolve_paths(args)
        text = build_channel(args, repo_root)
    except ChannelMismatch as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_FAIL
    emit(text, args.output)
    return EXIT_PASS


# ─── Main ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(
        description="AirymaxOS 发布物料清单门禁与发布辅助工具 "
                    "(09-release-process.md §3.1/§3.2/§3.7)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="物料清单门禁的 CI 模式（精简输出，仅退出码）")
    mode.add_argument("--checksums", action="store_true",
                      help="生成 sha256sum 校验和文本")
    mode.add_argument("--notes", action="store_true",
                      help="生成精简 release notes（概述 / 说明 / 其他）")
    mode.add_argument("--channel", action="store_true",
                      help="生成发行通道清单 channel.json"
                           "（04-update-mechanism §2.3）")
    mode.add_argument("--replay", metavar="ID", default=None,
                      help="按 produce 记录重放指定 active 物料的生成命令")
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="物料清单"
                             "（默认：tools/release-engineering/manifest.yaml）")
    parser.add_argument("--output", default=None, help="结果写入文件（默认标准输出）")
    parser.add_argument("--version", default=None,
                        help="release notes / channel.json 的版本号")
    parser.add_argument("--iso", default=None,
                        help="--channel 的 ISO 文件（默认：与账本同名的同目录文件）")
    parser.add_argument("--iso-oci", default=None,
                        help=f"ISO 账本（默认：{ISO_LEDGER_REL}）")
    parser.add_argument("--kernel-rpm", default=None,
                        help="内核 RPM（默认：dist/rpm/x86_64 下唯一一份）")
    parser.add_argument("--release-rpm", default=None,
                        help="airymaxos-release RPM（默认：dist/rpm 下唯一一份）")
    parser.add_argument("--build", type=int, default=None,
                        help="期望构建号；给出时须与 airymaxos-release NEVRA 相等")
    parser.add_argument("--notes-url", default=None,
                        help="发行说明地址（默认：GitHub Release tag 页）")
    parser.add_argument("--released-at", default=None,
                        help="UTC 时间戳（默认：SOURCE_DATE_EPOCH，否则当前时间）")
    parser.add_argument("--tag", default=None,
                        help="--replay 的 ${tag} 取值（默认 v<version>）")
    parser.add_argument("--repo", default=None,
                        help=f"--replay 的 ${{repo}} 取值（默认：{DEFAULT_REPO}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印展开后的命令，不执行（与 --replay 同用）")
    parser.add_argument("files", nargs="*", help="--checksums 的待校验文件")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数序列；None 表示使用 `sys.argv[1:]`。

    Returns:
        进程退出码（0 通过 / 1 检查失败 / 2 用法或环境错误）。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _dispatch(args: Any) -> int:
    """按互斥模式分派子命令，未指定模式时执行默认检查。"""
    for flag, handler in (
        ("checksums", cmd_checksums),
        ("notes", cmd_notes),
        ("channel", cmd_channel),
        ("replay", cmd_replay),
    ):
        if getattr(args, flag):
            return handler(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
