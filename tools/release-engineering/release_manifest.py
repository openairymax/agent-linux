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
  3. --notes     按 §3.7 从 git 提交区间派生结构化 release notes（版本概述、
                 新特性、修复的缺陷、安全修复、已知问题、升级说明、贡献者列表、
                 物料校验和）。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；只做「清单 ↔ 发布流水线/仓库」的交叉比对，不重复实现
    流水线逻辑，也不在清单里重复登记 release.yml 已自校验的 tag 形态等约束。
  * 严禁桩实现：所有检查均读取真实文件与真实 git 历史，给出可复现的判定与证据。

权威依据：
  docs/docs-linux/120-development-process/09-release-process.md
    §3.1 物料清单总览（12 项）/ §3.2 源码 tarball 与校验和 / §3.7 release notes
  docs/docs-linux/70-build-system/03-ci-cd-pipeline.md（SDK tarball / OCI 镜像）
  docs/docs-linux/190-distribution/06-sbom-generation.md（SBOM 格式）

Usage:
    python3 tools/release-engineering/release_manifest.py [options]
    python3 tools/release-engineering/release_manifest.py --checksums
        [options] FILE...
    python3 tools/release-engineering/release_manifest.py --notes [options]

Options:
    --check               物料清单门禁的 CI 模式（精简输出，仅退出码）
    --checksums           生成 sha256sum 校验和文本
    --notes               生成结构化 release notes
    --repo-root PATH      agent-linux 仓库根（默认：自动探测）
    --manifest PATH       物料清单（默认：tools/release-engineering/manifest.yaml）
    --output PATH         结果写入文件（默认：标准输出）
    --version VER         release notes 的版本号（--notes 必填）
    --rev-range RANGE     git 提交区间，如 v0.0.2..v0.0.3（--notes，默认 HEAD）
    --checksums-file F    嵌入 release notes 的校验和文件（--notes，可选）

Exit codes:
    0 — 检查通过 / 生成成功
    1 — 一项或多项检查失败
    2 — 用法/环境错误（清单缺失、YAML 非法、文件不可读、git 不可用等）

Example:
    >>> main(["--check"])
    0

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

__all__ = [
    "CheckResult",
    "ToolError",
    "build_checks",
    "build_parser",
    "build_sums",
    "check_pipeline",
    "check_schema",
    "classify_commit",
    "collect_git_log",
    "find_repo_root",
    "load_yaml",
    "main",
    "normalize_glob",
    "pipeline_globs",
    "print_report",
    "render_notes",
    "resolve_paths",
    "sha256_of",
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

TOP_KEYS = frozenset({"version", "materials", "notes"})
MATERIAL_KEYS = frozenset({
    "id", "name", "format", "signature", "authority", "delivery",
    "evidence", "defer_reason", "defer_target",
})
REQUIRED_MATERIAL_KEYS = ("id", "name", "format", "signature",
                          "authority", "delivery")
NOTES_KEYS = frozenset({"known_issues", "upgrade_notes"})

UPLOAD_ARTIFACT_USES = "actions/upload-artifact"
GH_RELEASE_USES = "softprops/action-gh-release"
TEMPLATE_RE = re.compile(r"\$\{\{[^}]*\}\}")

CVE_RE = re.compile(r"CVE-\d{4}-\d+")
FIX_RE = re.compile(r"\bfix(?:es|ed)?\b", re.IGNORECASE)
FEATURE_RE = re.compile(r"\b(?:add|feat|implement|introduce|support)\b",
                        re.IGNORECASE)

NOTES_EMPTY = "（无）"
NOTES_NOT_PROVIDED = "（未提供）"

# ─── Types ─────────────────────────────────────────────────────────────────

Manifest = dict[str, Any]
Globs = set[str]
Commit = tuple[str, str, str]
Commits = list[Commit]

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

    notes 是 release notes「已知问题」「升级说明」两章节的唯一事实源，
    两键均须显式声明为字符串列表；确无此类条目时用空列表表达，不得缺键，
    以杜绝「章节静默为空」的漂移。

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


def collect_git_log(repo_root: Path, rev_range: str) -> Commits:
    """读取 git 提交（跳过 merge）。

    Args:
        repo_root: 仓库根路径。
        rev_range: git 提交区间。

    Returns:
        `(hash, subject, author)` 三元组列表。

    Raises:
        ToolError: git 不可用或提交区间非法。
    """
    fmt = "%H%x1f%s%x1f%an"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--no-merges",
             f"--format={fmt}", rev_range],
            capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ToolError("git 不可用：请安装 git 后重试") from exc
    if proc.returncode != 0:
        raise ToolError(f"git log 失败（{rev_range}）：{proc.stderr.strip()}")
    commits: Commits = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


def classify_commit(subject: str) -> str:
    """按标题归类提交。

    Args:
        subject: 提交标题。

    Returns:
        `security` / `fix` / `feature`（无法识别时归入 feature）。
    """
    if CVE_RE.search(subject) or re.match(r"^\s*security\b", subject,
                                          re.IGNORECASE):
        return "security"
    if FIX_RE.search(subject):
        return "fix"
    return "feature"


def _bullet_list(items: Sequence[str]) -> str:
    """把条目渲染为 Markdown 列表；空则返回占位符。"""
    if not items:
        return NOTES_EMPTY
    return "\n".join(f"- {item}" for item in items)


def _contributors(commits: Commits) -> list[str]:
    """按贡献者聚合提交数，输出 `姓名 (N)` 列表。"""
    counts: dict[str, int] = {}
    for _, _, author in commits:
        counts[author] = counts.get(author, 0) + 1
    return [f"{name} ({counts[name]})" for name in sorted(counts)]


def _disclosure(notes: Manifest, key: str) -> str:
    """渲染 notes 披露章节内容。

    Args:
        notes: 清单 notes 段。
        key: 披露键（NOTES_KEYS 之一）。

    Returns:
        条目列表；键缺失时为「未提供」，空列表时为「无」。
    """
    items = notes.get(key)
    if items is None:
        return NOTES_NOT_PROVIDED
    return _bullet_list(items)


def render_notes(
    version: str,
    rev_range: str,
    commits: Commits,
    checksum_text: str,
    notes: Manifest | None = None,
) -> str:
    """按 §3.7 结构渲染 release notes Markdown。

    Args:
        version: 版本号。
        rev_range: git 提交区间。
        commits: 提交三元组列表。
        checksum_text: 物料校验和文本（空表示未提供）。
        notes: 清单 notes 披露段（「已知问题」/「升级说明」的唯一事实源）。

    Returns:
        完整的 release notes Markdown。
    """
    buckets = {"security": [], "fix": [], "feature": []}
    for _, subject, _ in commits:
        buckets[classify_commit(subject)].append(subject)

    checksum_block = checksum_text.strip() or NOTES_NOT_PROVIDED
    disclosures = notes or {}
    sections = [
        ("版本概述",
         f"- 版本号：{version}\n- 发布日期：{date.today().isoformat()}\n"
         f"- 提交区间：{rev_range}\n- 提交数量：{len(commits)}"),
        ("新特性", _bullet_list(buckets["feature"])),
        ("修复的缺陷", _bullet_list(buckets["fix"])),
        ("安全修复", _bullet_list(buckets["security"])),
        ("已知问题", _disclosure(disclosures, "known_issues")),
        ("升级说明", _disclosure(disclosures, "upgrade_notes")),
        ("贡献者列表", _bullet_list(_contributors(commits))),
        ("物料校验和", f"```\n{checksum_block}\n```"),
    ]
    body = "\n\n".join(
        f"## {title}\n\n{content}" for title, content in sections)
    return f"# {version} Release Notes\n\n{body}\n"


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
    """生成结构化 release notes。"""
    if not args.version:
        print("ERROR: --notes 需要 --version 指定版本号", file=sys.stderr)
        return EXIT_USAGE
    try:
        repo_root, manifest_path = resolve_paths(args)
        manifest = load_yaml(manifest_path, "物料清单")
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    notes = manifest.get("notes")
    if not isinstance(notes, dict):
        print("ERROR: 物料清单缺少 notes 披露段："
              "「已知问题」/「升级说明」须以 SSoT 声明（§3.7）", file=sys.stderr)
        return EXIT_USAGE
    rev_range = args.rev_range or "HEAD"
    checksum_text = ""
    if args.checksums_file:
        try:
            checksum_text = Path(args.checksums_file).read_text(
                encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: 校验和文件无法读取：{exc}", file=sys.stderr)
            return EXIT_USAGE
    commits = collect_git_log(repo_root, rev_range)
    emit(render_notes(args.version, rev_range, commits, checksum_text, notes),
         args.output)
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
                      help="生成结构化 release notes")
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="物料清单"
                             "（默认：tools/release-engineering/manifest.yaml）")
    parser.add_argument("--output", default=None, help="结果写入文件（默认标准输出）")
    parser.add_argument("--version", default=None, help="release notes 版本号")
    parser.add_argument("--rev-range", default=None,
                        help="git 提交区间，如 v0.0.2..v0.0.3（默认 HEAD）")
    parser.add_argument("--checksums-file", default=None,
                        help="嵌入 release notes 的校验和文件")
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
        return cmd_check(args)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
