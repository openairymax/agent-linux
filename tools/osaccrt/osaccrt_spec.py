# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_spec — NFR/OS-ACC 阈值表加载与校验。

阈值表以 TOML 随源码发布，运行时不做 Markdown 解析：NFR 原文数值在
评审时抄录为一条 [[threshold]]，`source` 指向条目标号，`limit` 与
`unit` 保持原文语义。加载即校验 schema 版本、字段集合、键命名、来源
编号、比较表达式与键唯一性，任一不满足即拒绝整表，杜绝带病阈值进入
实验执行。

表按实验矩阵闭合：E1~E7 引用的 NFR 编号都有条目，未引用的 NFR 不
收录，避免出现没有实验承担的死阈值。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §2 实验大纲矩阵、§3.1 六步规程、§5.1 TV-01-05
    docs/docs-linux/00-requirements/03-non-functional-requirements.md
        NFR-P-001~P-006、NFR-S-001、NFR-R-001

Example:
    >>> table = load_spec()
    >>> table.get("boot.cold").limit
    '< 30'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from osaccrt_model import Threshold, need_text

__all__ = ["DEFAULT_SPEC", "SPEC_SCHEMA", "SpecTable", "load_spec", "parse_spec"]

SPEC_SCHEMA: Final[int] = 1
DEFAULT_SPEC: Final[Path] = Path(__file__).with_name("specs") / "nfr.toml"
KEY_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
SRC_RE: Final[re.Pattern[str]] = re.compile(r"(?:NFR-[A-Z]-\d{3}|OS-ACC-[A-Z]+-\d{3})")
LIMIT_RE: Final[re.Pattern[str]] = re.compile(r"(?:<=|>=|<|>|=) -?\d+(?:\.\d+)?")
_FIELDS: Final[frozenset[str]] = frozenset({"key", "source", "limit", "unit"})
_ROOTS: Final[frozenset[str]] = frozenset({"schema", "threshold"})


@dataclass(frozen=True)
class SpecTable:
    """阈值表快照，构造即校验版本、条目类型与键唯一性。"""

    schema: int
    thresholds: tuple[Threshold, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema, bool) or self.schema != SPEC_SCHEMA:
            raise ValueError(f"unsupported schema: {self.schema!r}")
        if not isinstance(self.thresholds, tuple) or not self.thresholds:
            raise ValueError("thresholds must be a non-empty tuple")
        if not all(isinstance(item, Threshold) for item in self.thresholds):
            raise ValueError("thresholds must contain Threshold items")
        keys = self.keys
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate threshold keys: {keys}")

    @property
    def keys(self) -> tuple[str, ...]:
        """阈值键序列，供实验域按产物名对账。"""
        return tuple(item.key for item in self.thresholds)

    def get(self, key: str) -> Threshold:
        """按键取单条阈值；缺失即报错，避免调用方静默降级。"""
        for item in self.thresholds:
            if item.key == key:
                return item
        raise KeyError(f"unknown threshold key: {key!r}")

    def for_source(self, source: str) -> tuple[Threshold, ...]:
        """按 NFR/OS-ACC 编号取该来源的全部阈值。"""
        return tuple(item for item in self.thresholds if item.source == source)


def load_spec(path: str | Path = DEFAULT_SPEC) -> SpecTable:
    """读取 TOML 阈值表；读失败或语法错误按定位信息报 ValueError。"""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read spec {target}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid spec {target}: {exc}") from exc
    return parse_spec(data)


def parse_spec(data: Mapping[str, object]) -> SpecTable:
    """校验表结构并转换为阈值快照。"""
    if not isinstance(data, Mapping):
        raise ValueError("spec root must be a table")
    _need_keys(data, _ROOTS, "spec")
    schema = data.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise ValueError(f"schema must be an integer: {schema!r}")
    rows = data.get("threshold")
    if not isinstance(rows, list):
        raise ValueError("threshold must be an array of tables")
    items = tuple(_row(row, idx) for idx, row in enumerate(rows))
    return SpecTable(schema, items)


def _row(row: object, idx: int) -> Threshold:
    """校验单条阈值，错误信息带数组下标。"""
    if not isinstance(row, Mapping):
        raise ValueError(f"threshold[{idx}] must be a table")
    _need_keys(row, _FIELDS, f"threshold[{idx}]")
    for name in sorted(_FIELDS):
        need_text(row[name], f"threshold[{idx}].{name}")
    key, source, limit = row["key"], row["source"], row["limit"]
    _need_match(KEY_RE, key, f"threshold[{idx}].key")
    _need_match(SRC_RE, source, f"threshold[{idx}].source")
    _need_match(LIMIT_RE, limit, f"threshold[{idx}].limit")
    return Threshold(key, source, limit, row["unit"])


def _need_keys(data: Mapping[str, object], fields: frozenset[str], where: str) -> None:
    """字段集合必须与 schema 完全一致，拼写错误立即暴露。"""
    unknown = sorted(set(data) - fields)
    missing = sorted(fields - set(data))
    if unknown:
        raise ValueError(f"{where} has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"{where} is missing fields: {missing}")


def _need_match(pattern: re.Pattern[str], value: object, where: str) -> None:
    """文本必须整体匹配给定模式。"""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{where} is malformed: {value!r}")
