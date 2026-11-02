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

Options:
    --check               物料清单门禁的 CI 模式（精简输出，仅退出码）
    --checksums           生成 sha256sum 校验和文本
    --notes               生成精简 release notes（概述 / 说明 / 其他）
    --channel             生成发行通道清单 channel.json
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
    "check_schema",
    "find_repo_root",
    "iso_asset",
    "load_yaml",
    "main",
    "normalize_glob",
    "parse_nevra",
    "pick_rpm",
    "pipeline_globs",
    "print_report",
    "read_ledger",
    "render_notes",
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
    "evidence", "defer_reason", "defer_target",
})
REQUIRED_MATERIAL_KEYS = ("id", "name", "format", "signature",
                          "authority", "delivery")
NOTES_KEYS = frozenset({"summary", "remarks"})
NOTES_MAX_LINES = 3

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
    if yaml is None:
        raise ToolError("PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ToolError(f"{label}文件不存在：{path}") from exc
    except OSError as exc:
        raise ToolError(f"{label}文件无法读取：{path}: {exc}") from exc
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
        steps = job.get("steps") if isinstance(job, dict) else None
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            uses = str(step.get("uses", ""))
            params = step.get("with") or {}
            if uses.startswith(UPLOAD_ARTIFACT_USES):
                globs |= _split_globs(params.get("path"))
            elif uses.startswith(GH_RELEASE_USES):
                globs |= _split_globs(params.get("files"))
    if not globs:
        raise ToolError(f"发布流水线未声明任何产物（upload-artifact / release）："
                        f"{path}")
    return globs


# ─── Schema check ──────────────────────────────────────────────────────────


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

    problems = []
    unknown = set(entry) - MATERIAL_KEYS
    if unknown:
        problems.append(f"{where} 未知字段: {', '.join(sorted(unknown))}")
    for key in REQUIRED_MATERIAL_KEYS:
        if not entry.get(key):
            problems.append(f"{where} 缺少或为空值的必填字段: {key}")

    mid = entry.get("id")
    if isinstance(mid, str) and mid:
        if mid in seen:
            problems.append(f"{where}.id 重复: {mid}")
        else:
            seen.add(mid)

    delivery = entry.get("delivery")
    if delivery is not None and delivery not in DELIVERY_CHOICES:
        problems.append(f"{where}.delivery 必须是 {' 或 '.join(DELIVERY_CHOICES)}")
    problems.extend(_delivery_errs(where, entry, delivery))
    return problems


def _delivery_errs(where: str, entry: Manifest, delivery: Any) -> list[str]:
    """按 delivery 状态校验 evidence / defer_* 字段的组合合法性。"""
    problems = []
    evidence = entry.get("evidence")
    if delivery == DELIVERY_ACTIVE:
        if not isinstance(evidence, list) or not evidence:
            problems.append(f"{where}: active 物料要求非空 evidence 列表")
        elif any(not isinstance(g, str) or not g for g in evidence):
            problems.append(f"{where}.evidence 含非法项")
        if entry.get("defer_reason") or entry.get("defer_target"):
            problems.append(
                f"{where}: active 物料不得登记 defer_reason/defer_target")
    elif delivery == DELIVERY_DEFERRED:
        for key in ("defer_reason", "defer_target"):
            if not entry.get(key):
                problems.append(f"{where}: deferred 物料要求 {key}")
        if evidence:
            problems.append(f"{where}: deferred 物料不得登记 evidence")
    return problems


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

    problems = []
    unknown = set(notes) - NOTES_KEYS
    if unknown:
        problems.append(f"notes 未知字段: {', '.join(sorted(unknown))}")
    for key in sorted(NOTES_KEYS):
        value = notes.get(key)
        if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip()
                for item in value):
            problems.append(
                f"notes.{key} 必须是字符串列表且条目非空"
                "（确无此类条目时用空列表）")
        elif len(value) > NOTES_MAX_LINES:
            problems.append(
                f"notes.{key} 至多 {NOTES_MAX_LINES} 条（当前 {len(value)} 条）")
    return problems


def check_schema(manifest: Manifest) -> CheckResult:
    """校验清单 schema。

    覆盖顶层字段、必填字段、id 唯一、delivery 取值与配套字段。

    Args:
        manifest: 清单顶层映射。

    Returns:
        检查结果。
    """
    problems = []
    unknown = set(manifest) - TOP_KEYS
    if unknown:
        problems.append(f"顶层未知字段: {', '.join(sorted(unknown))}")
    if not manifest.get("version"):
        problems.append("缺少或为空值的必填字段: version")
    problems.extend(_notes_errs(manifest.get("notes")))

    materials = manifest.get("materials")
    if not isinstance(materials, list) or not materials:
        problems.append("materials 必须是非空列表")
    else:
        seen: set[str] = set()
        for idx, entry in enumerate(materials):
            problems.extend(_entry_problems(idx, entry, seen))

    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


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

    problems = []
    evidenced: Globs = set()
    for item in manifest.get("materials", []):
        if item.get("delivery") != DELIVERY_ACTIVE:
            continue
        for raw in item.get("evidence", []):
            glob = normalize_glob(raw)
            evidenced.add(glob)
            if glob not in declared:
                problems.append(
                    f"active 物料 {item.get('id')} 的产物 {glob} 未在 release.yml 声明")

    for glob in sorted(declared - evidenced):
        problems.append(f"release.yml 产出 {glob} 未在清单登记（未登记物料）")

    return CheckResult(name, not problems, "\n".join(problems))


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
    disclosures: Manifest = notes or {}
    sections = [
        ("概述", f"- 版本号 v{version}\n- 发布日期 {date.today():%Y%m}"),
        ("说明", _bullet_list(disclosures.get("summary") or [])),
        ("其他", _bullet_list(disclosures.get("remarks") or [])),
    ]
    body = "\n\n".join(
        f"## {title}\n{content}" for title, content in sections)
    return f"{body}\n"


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
        if failed == 0:
            print(f"RELEASE-MANIFEST: PASS ({passed}/{total} checks)")
            return
        print(f"RELEASE-MANIFEST: FAIL ({passed}/{total} checks, "
              f"{failed} failed)")
        for result in results:
            if not result.passed:
                print(f"  FAIL: {result.name}")
                for line in result.details.splitlines():
                    print(f"    {line}")
        return

    print("=" * 72)
    print("agent-linux AirymaxOS 发布物料清单报告")
    print(f"清单版本: {manifest.get('version', '?')}    "
          f"物料数: {len(manifest.get('materials', []))}")
    print("=" * 72)
    print()
    for result in results:
        marker = "[+]" if result.passed else "[-]"
        status = "PASS" if result.passed else "FAIL"
        print(f"{marker} {status}: {result.name}")
        for line in result.details.splitlines():
            print(f"      {line}")
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
    return [schema, check_pipeline(manifest, repo_root)]


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

    ledger: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() in LEDGER_KEYS:
            ledger.setdefault(key.strip(), value.strip())

    missing = [key for key in LEDGER_KEYS if key not in ledger]
    if missing:
        raise ToolError(f"ISO 账本缺少字段 {'/'.join(missing)}：{path}")
    if not DIGEST_RE.match(ledger["sha256"]):
        raise ToolError(f"ISO 账本 sha256 非法：{ledger['sha256']}")
    if not ledger["size"].isdigit():
        raise ToolError(f"ISO 账本 size 非法：{ledger['size']}")
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
    if iso.name != ledger["name"]:
        raise ChannelMismatch(
            f"ISO 文件名与账本不一致：{iso.name} != {ledger['name']}")
    size = iso.stat().st_size
    if size != int(ledger["size"]):
        raise ChannelMismatch(
            f"ISO 大小与账本不一致：{size} != {ledger['size']}")
    digest = sha256_of(iso)
    if digest != ledger["sha256"]:
        raise ChannelMismatch(
            f"ISO 摘要与账本不一致：{digest} != {ledger['sha256']}")
    return {"name": iso.name, "size": size, "sha256": digest}


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
        if not STAMP_RE.match(raw):
            raise ToolError(f"--released-at 须为 {STAMP_FMT} 形态：{raw}")
        return raw
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            moment = datetime.fromtimestamp(int(epoch), timezone.utc)
        except ValueError as exc:
            raise ToolError(f"SOURCE_DATE_EPOCH 非法：{epoch}") from exc
        return moment.strftime(STAMP_FMT)
    return datetime.now(timezone.utc).strftime(STAMP_FMT)


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
    ledger_path = (Path(args.iso_oci) if args.iso_oci
                   else repo_root / ISO_LEDGER_REL)
    ledger = read_ledger(ledger_path)
    iso = Path(args.iso) if args.iso else ledger_path.parent / ledger["name"]

    kernel_path = (Path(args.kernel_rpm) if args.kernel_rpm
                   else pick_rpm(repo_root, KERNEL_RPM_GLOB, "内核 RPM"))
    release_path = (Path(args.release_rpm) if args.release_rpm
                    else pick_rpm(repo_root, RELEASE_RPM_GLOB,
                                  "airymaxos-release RPM"))
    kernel = parse_nevra(kernel_path)
    release = parse_nevra(release_path)

    if args.version not in (kernel.version, release.version):
        raise ChannelMismatch(
            f"版本号与制品不一致：--version {args.version}，"
            f"内核 {kernel.version}，发行包 {release.version}")
    if args.build is not None and args.build != release.build:
        raise ChannelMismatch(
            f"构建号与发行包 NEVRA 不一致：--build {args.build} "
            f"!= {release.build}")

    notes_url = args.notes_url or CHANNEL_URL.format(ver=args.version)
    if not notes_url.startswith(CHANNEL_DOMAIN):
        raise ToolError(f"notes_url 须落在发行域 {CHANNEL_DOMAIN}：{notes_url}")

    payload = {
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
        "notes_url": notes_url,
    }
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
        if args.checksums:
            return cmd_checksums(args)
        if args.notes:
            return cmd_notes(args)
        if args.channel:
            return cmd_channel(args)
        return cmd_check(args)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
