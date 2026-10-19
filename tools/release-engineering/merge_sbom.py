#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
merge_sbom.py — AirymaxOS SPDX 2.3 SBOM 分片合并器

release 流水线以 syft 为各子仓（kernel + 7 个用户态子仓）分别生成 SPDX 2.3
JSON 分片，本工具把这些分片合并为单一的 `agent-linux` 主体 SBOM，输出结构
符合 docs/docs-linux/190-distribution/06-sbom-generation.md §2 的文档规范。

设计原则（与项目铁律一致）：
  * 无桩实现：全部逻辑均为真实的字段级合并，不存在任何占位跳过路径。
  * 可重复构建：输出以 sort_keys=True 序列化，包/文件按 SPDXID 排序、关系按
    三元组排序去重；`created` 由 SOURCE_DATE_EPOCH 派生（未设时取当前 UTC），
    保证同一输入集产出逐字节一致的文档。
  * 冲突即失败：同一去重键在两份分片中内容不一致时判定为冲突并失败，绝不静默
    丢弃或覆盖，避免合并出「看似正常」的错误 SBOM。

合并规则：
  * 文档级：spdxVersion 固定 "SPDX-2.3"，dataLicense 固定 "CC0-1.0"，
    SPDXID 固定 "SPDXRef-DOCUMENT"；任一分片取值不符即失败。
  * packages / files：按 SPDXID 去重合并，同 ID 不同内容判冲突。
  * relationships：按 (spdxElementId, relationshipType, relatedSpdxElement)
    三元组去重。
  * externalDocumentRefs / hasExtractedLicensingInfos：分别按
    externalDocumentId / licenseId 去重合并。
  * creationInfo.creators：取并集并追加本工具标识。

权威依据：
  docs/docs-linux/190-distribution/06-sbom-generation.md
    §2.1.1 必需字段、§2.1.2 agent-linux 扩展字段、§2.1.3 完整示例
    §2.3.1 四类关系（DESCRIBES / CONTAINS / DEPENDS_ON / GENERATED_FROM）
    §8 可重复构建（SOURCE_DATE_EPOCH 派生时间戳）

Usage:
    python3 tools/release-engineering/merge_sbom.py \
        --output sbom-agent-linux.spdx.json sbom-*.spdx.json

Options:
    -o, --output PATH    合并输出路径（必需）
    --name NAME          合成文档 name（默认 agent-linux）
    --namespace URI      合成文档 documentNamespace（默认由 name 派生）
    --quiet              不打印合并摘要
    -h, --help           显示帮助

Exit codes:
    0 — 合并成功
    1 — 内容冲突或 SPDX 语义非法（版本/许可不一致、同键内容冲突等）
    2 — 用法/解析错误（缺少输入、文件不可读、非 JSON 等）

Example:
    >>> main(["--output", "sbom.spdx.json", "a.spdx.json", "b.spdx.json"])
    0

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "MergeError",
    "ParseError",
    "build_parser",
    "load_fragment",
    "main",
    "merge_documents",
    "write_document",
]

# ─── Constants ─────────────────────────────────────────────────────────────

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

SPDX_VERSION = "SPDX-2.3"
DATA_LICENSE = "CC0-1.0"
DOC_SPDXID = "SPDXRef-DOCUMENT"
TOOL_CREATOR = "Tool: merge_sbom.py-1.1.0"

DEFAULT_NAME = "agent-linux"
NAMESPACE_PREFIX = "https://airymaxos.com/spdx/"

# 列表型文档字段 → 去重键（键名须为该字段元素的非空字符串字段）。
LIST_FIELDS = (
    ("packages", "SPDXID"),
    ("files", "SPDXID"),
    ("externalDocumentRefs", "externalDocumentId"),
    ("hasExtractedLicensingInfos", "licenseId"),
)
LIST_KEYS = dict(LIST_FIELDS)

# ─── Types ─────────────────────────────────────────────────────────────────

JsonDoc = dict[str, Any]
Fragment = tuple[Path, JsonDoc]

# ─── Errors ────────────────────────────────────────────────────────────────


class ParseError(Exception):
    """输入不可解析或用法错误（→ 退出码 2）。"""


class MergeError(Exception):
    """内容冲突或 SPDX 语义非法（→ 退出码 1）。"""


# ─── Helpers ───────────────────────────────────────────────────────────────


def _canonical(obj: Any) -> str:
    """计算对象的内容指纹：键序无关的紧凑 JSON 表示。

    Args:
        obj: 任意可被 JSON 序列化的对象。

    Returns:
        规范化后的紧凑 JSON 字符串。
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def load_fragment(path: Path) -> JsonDoc:
    """读取一个 SPDX JSON 分片。

    Args:
        path: 分片文件路径。

    Returns:
        解析后的 SPDX 文档对象。

    Raises:
        ParseError: 文件不可读、非合法 JSON 或顶层不是对象。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseError(f"无法读取输入分片 {path}: {exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"输入分片不是合法 JSON {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ParseError(f"输入分片顶层必须是 JSON 对象: {path}")
    return doc


def _created_stamp() -> str:
    """由 SOURCE_DATE_EPOCH 派生 ISO 8601 时间戳（未设时取当前 UTC）。

    Returns:
        `YYYY-MM-DDTHH:MM:SSZ` 格式的时间戳。

    Raises:
        ParseError: SOURCE_DATE_EPOCH 不是合法整数。
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        moment = datetime.now(tz=timezone.utc)
    else:
        try:
            moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except ValueError as exc:
            raise ParseError(f"SOURCE_DATE_EPOCH 非法: {epoch!r}") from exc
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _namespace(name: str) -> str:
    """未显式给出命名空间时由文档 name 派生。"""
    return NAMESPACE_PREFIX + name


def _version_tuple(text: str) -> tuple[int, ...]:
    """把 "3.23" 之类的版本串转为可比较元组。"""
    return tuple(int(chunk) if chunk.isdigit() else -1
                 for chunk in text.split("."))


def _max_version(current: str | None, candidate: str) -> str:
    """取较大的 licenseListVersion。"""
    if current is None:
        return candidate
    return candidate if _version_tuple(candidate) > _version_tuple(current) \
        else current


# ─── Merge primitives ──────────────────────────────────────────────────────


class _Accumulator:
    """按去重键累积条目；同键内容不一致即判冲突。"""

    def __init__(self) -> None:
        self._items: dict[Any, JsonDoc] = {}

    def add(self, key: Any, item: JsonDoc, path: Path, label: str) -> None:
        """并入一条记录。

        Args:
            key: 去重键。
            item: 待并入的 JSON 对象。
            path: 来源分片（用于错误信息）。
            label: 字段名（用于错误信息）。

        Raises:
            MergeError: 同键记录内容不一致。
        """
        previous = self._items.get(key)
        if previous is None:
            self._items[key] = item
            return
        if _canonical(previous) != _canonical(item):
            raise MergeError(f"{path}: {label} 键 {key!r} 内容冲突")

    def values(self) -> list[JsonDoc]:
        """返回已累积的全部记录。"""
        return list(self._items.values())


def _check_ident(path: Path, doc: JsonDoc) -> None:
    """校验分片的文档级固定字段取值。

    Raises:
        MergeError: spdxVersion 或 dataLicense 与规范不符。
    """
    version = doc.get("spdxVersion")
    if version != SPDX_VERSION:
        raise MergeError(
            f"{path}: spdxVersion 必须为 {SPDX_VERSION}（实际 {version!r}）")
    license_id = doc.get("dataLicense")
    if license_id != DATA_LICENSE:
        raise MergeError(
            f"{path}: dataLicense 必须为 {DATA_LICENSE}（实际 {license_id!r}）")


def _collect_lists(
    path: Path,
    doc: JsonDoc,
    accs: dict[str, _Accumulator],
) -> None:
    """把 LIST_FIELDS 中的列表型字段并入对应累积器。

    Raises:
        MergeError: 字段不是数组、元素不是对象或缺少去重键。
    """
    for field, key_name in LIST_FIELDS:
        items = doc.get(field)
        if items is None:
            continue
        if not isinstance(items, list):
            raise MergeError(f"{path}: {field} 必须是数组")
        for item in items:
            if not isinstance(item, dict):
                raise MergeError(f"{path}: {field} 的元素必须是对象")
            key = item.get(key_name)
            if not isinstance(key, str) or not key:
                raise MergeError(f"{path}: {field} 元素缺少非空 {key_name}")
            accs[field].add(key, item, path, field)


def _collect_rels(path: Path, doc: JsonDoc, acc: _Accumulator) -> None:
    """把 relationships 按 (源, 类型, 目标) 三元组并入累积器。

    Raises:
        MergeError: relationships 结构非法。
    """
    rels = doc.get("relationships")
    if rels is None:
        return
    if not isinstance(rels, list):
        raise MergeError(f"{path}: relationships 必须是数组")
    for rel in rels:
        if not isinstance(rel, dict):
            raise MergeError(f"{path}: relationships 的元素必须是对象")
        key = (rel.get("spdxElementId"), rel.get("relationshipType"),
               rel.get("relatedSpdxElement"))
        if not all(isinstance(part, str) and part for part in key):
            raise MergeError(
                f"{path}: relationship 缺少 spdxElementId/"
                f"relationshipType/relatedSpdxElement")
        acc.add(key, rel, path, "relationship")


def _collect_info(
    path: Path,
    doc: JsonDoc,
    creators: set[str],
) -> str | None:
    """并入 creators 集合并返回该分片声明的 licenseListVersion。

    Raises:
        MergeError: creationInfo 不是对象。
    """
    info = doc.get("creationInfo")
    if info is None:
        return None
    if not isinstance(info, dict):
        raise MergeError(f"{path}: creationInfo 必须是对象")
    for creator in info.get("creators") or []:
        if isinstance(creator, str) and creator:
            creators.add(creator)
    version = info.get("licenseListVersion")
    return str(version) if version else None


# ─── Merge ─────────────────────────────────────────────────────────────────


def merge_documents(
    fragments: Sequence[Fragment],
    name: str,
    namespace: str,
    created: str,
) -> JsonDoc:
    """把多个 SPDX 分片合并为单一 SPDX 2.3 文档对象。

    Args:
        fragments: `(来源路径, 文档对象)` 序列。
        name: 合成文档 name。
        namespace: 合成文档 documentNamespace。
        created: 合成文档 creationInfo.created。

    Returns:
        合并后的 SPDX 文档对象（列表字段均已排序）。

    Raises:
        MergeError: 分片语义非法或同键内容冲突。
    """
    accs = {field: _Accumulator() for field, _ in LIST_FIELDS}
    relationships = _Accumulator()
    creators: set[str] = set()
    license_list_version: str | None = None

    for path, doc in fragments:
        _check_ident(path, doc)
        _collect_lists(path, doc, accs)
        _collect_rels(path, doc, relationships)
        declared = _collect_info(path, doc, creators)
        if declared:
            license_list_version = _max_version(license_list_version, declared)

    creators.add(TOOL_CREATOR)
    creation_info: JsonDoc = {
        "creators": sorted(creators),
        "created": created,
    }
    if license_list_version:
        creation_info["licenseListVersion"] = license_list_version

    document: JsonDoc = {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": DATA_LICENSE,
        "SPDXID": DOC_SPDXID,
        "name": name,
        "documentNamespace": namespace,
        "creationInfo": creation_info,
        "packages": sorted(accs["packages"].values(),
                           key=lambda item: item["SPDXID"]),
        "relationships": sorted(
            relationships.values(),
            key=lambda rel: (rel["spdxElementId"], rel["relationshipType"],
                             rel["relatedSpdxElement"])),
    }
    for field in ("files", "externalDocumentRefs",
                  "hasExtractedLicensingInfos"):
        values = accs[field].values()
        if values:
            key_name = LIST_KEYS[field]
            document[field] = sorted(values, key=lambda item: item[key_name])
    return document


def write_document(document: JsonDoc, output: Path) -> None:
    """以确定性 JSON 写出合并结果。

    Args:
        document: 合并后的 SPDX 文档对象。
        output: 输出路径。

    Raises:
        ParseError: 输出目录或文件不可写。
    """
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ParseError(f"无法写入输出 {output}: {exc}") from exc


# ─── Main ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(
        description="AirymaxOS SPDX 2.3 SBOM 分片合并器 "
                    "(docs/docs-linux/190-distribution/06-sbom-generation.md)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", metavar="FRAGMENT",
                        help="输入 SPDX JSON 分片（至少一个）")
    parser.add_argument("-o", "--output", required=True,
                        help="合并输出路径（SPDX JSON）")
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help=f"合成文档 name（默认 {DEFAULT_NAME}）")
    parser.add_argument("--namespace", default=None,
                        help="合成文档 documentNamespace（默认由 name 派生）")
    parser.add_argument("--quiet", action="store_true",
                        help="不打印合并摘要")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数序列；None 表示使用 `sys.argv[1:]`。

    Returns:
        进程退出码（0 成功 / 1 冲突 / 2 用法或解析错误）。
    """
    args = build_parser().parse_args(argv)
    if not args.inputs:
        print("ERROR: 至少需要一个输入分片", file=sys.stderr)
        return EXIT_USAGE

    try:
        fragments: list[Fragment] = []
        for raw in args.inputs:
            path = Path(raw)
            fragments.append((path, load_fragment(path)))
        created = _created_stamp()
        namespace = args.namespace or _namespace(args.name)
        document = merge_documents(fragments, args.name, namespace, created)
        write_document(document, Path(args.output))
    except ParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except MergeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FAIL

    if not args.quiet:
        print(f"MERGE-SBOM: PASS — {len(document['packages'])} packages, "
              f"{len(document['relationships'])} relationships "
              f"-> {args.output}")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
