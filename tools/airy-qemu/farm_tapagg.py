# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_tapagg — QEMU 测试农场的 TAP 聚合。

运行器把 kselftest 的 TAP 流落盘后，本模块负责把原始文本聚合成可判定的
证据：全局计划数、通过/失败/跳过计数，以及按套件拆分的明细。

kselftest 的 ``run_kselftest.sh`` 会为每个测试目录输出一个独立的 TAP 块
（``TAP version 13`` + ``1..N`` + 逐条 ``ok``/``not ok``），因此计划行会
重复出现。聚合必须累加全部计划行；只取最后一行会把多套件作业的计划数
算成最后一个套件的数量，使“缺失用例”永远为零。

用例描述由 runner.sh 固定为 ``selftests: <目录>: <测试>``，据此归属套件。
作业的期望用例数以预检证据 ``Preflight.suite_tests`` 为准：某个套件在
TAP 中完全没有结果时，该套件记为静默，其全部用例计入缺失，而不是被
误判为通过。

跳过用例（``ok N ... # SKIP``）单独计数，满足 OS-TEST-021 对 SKIP 的
单独统计要求；它们仍属于已执行用例，不构成失败。

本模块只做聚合，不做 PASS/FAIL 判定；判定策略留给门禁层。

权威依据：
  tools/parse-tap.py（计划行与结果行的解析契约）
  linux tools/testing/selftests/kselftest/runner.sh（run_many 输出格式）
  docs-linux/80-testing/02-kselftest.md OS-TEST-021（SKIP 单独计数）

Example:
    >>> report = collect_tap(raw_run)
    >>> report.summary.ok_count
    4
    >>> report.passed
    True

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# 2. 本工具模块导入
from farm_runner import RawRun
from farm_types import (
    AIRY_QEMU_ERROR,
    FarmConfigError,
    FarmError,
    TapSummary,
)

# 3. 公开导出
__all__ = [
    "SuiteTally",
    "TapReport",
    "collect_tap",
    "parse_tap",
    "read_tap",
]

# 4. TAP 行格式（与 tools/parse-tap.py 同源）
_PLAN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d+)\.\.(\d+)\s*(?:#.*)?$"
)
_RESULT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<status>ok|not ok)\s+(?P<num>\d+)"
    r"(?:\s+(?P<rest>.*))?$"
)
_SUITE_RE: Final[re.Pattern[str]] = re.compile(
    r"^selftests:\s*(?P<suite>[^:]+?)\s*:\s*(?P<test>\S.*)$"
)
_DIRECTIVE_RE: Final[re.Pattern[str]] = re.compile(
    r"\s+#\s*(?P<word>[A-Za-z]+)(?P<detail>.*)$"
)

# 5. 计数槽位（ok / not_ok / skip）
_OK: Final[int] = 0
_BAD: Final[int] = 1
_SKIP: Final[int] = 2


@dataclass(frozen=True)
class SuiteTally:
    """单个 kselftest 套件的 TAP 统计。"""

    name: str
    plan_total: int
    ok_count: int
    not_ok_count: int
    skip_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        for field in ("plan_total", "ok_count", "not_ok_count", "skip_count"):
            _need_count(getattr(self, field), field)
        if self.executed > self.plan_total:
            raise ValueError(
                f"executed exceeds plan_total: {self.executed} > "
                f"{self.plan_total}"
            )

    @property
    def executed(self) -> int:
        """已上报结果的用例数（含跳过）。"""
        return self.ok_count + self.not_ok_count + self.skip_count

    @property
    def missing(self) -> int:
        """计划中缺失结果的用例数。"""
        return self.plan_total - self.executed

    @property
    def complete(self) -> bool:
        """计划是否完整执行。"""
        return self.plan_total > 0 and self.missing == 0

    @property
    def passed(self) -> bool:
        """是否完整执行且无失败用例。"""
        return self.complete and self.not_ok_count == 0


@dataclass(frozen=True)
class TapReport:
    """全局 TAP 汇总与按套件明细（两者必须自洽）。"""

    summary: TapSummary
    suites: tuple[SuiteTally, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.summary, TapSummary):
            raise ValueError("summary must be TapSummary")
        if not isinstance(self.suites, tuple) or not self.suites:
            raise ValueError("suites must be a non-empty tuple")
        names: set[str] = set()
        for tally in self.suites:
            if not isinstance(tally, SuiteTally):
                raise ValueError("suites must contain SuiteTally entries")
            if tally.name in names:
                raise ValueError(f"duplicate suite: {tally.name!r}")
            names.add(tally.name)
        for field, total in self._sums():
            if total != getattr(self.summary, field):
                raise ValueError(
                    f"{field} mismatch: suites={total} "
                    f"summary={getattr(self.summary, field)}"
                )

    def _sums(self) -> tuple[tuple[str, int], ...]:
        """逐字段汇总套件计数，用于自洽校验。"""
        fields = ("plan_total", "ok_count", "not_ok_count", "skip_count")
        return tuple(
            (field, sum(getattr(tally, field) for tally in self.suites))
            for field in fields
        )

    @property
    def complete(self) -> bool:
        """计划是否完整执行。"""
        return self.summary.complete

    @property
    def passed(self) -> bool:
        """是否完整执行且无失败用例。"""
        return self.summary.passed

    @property
    def silent(self) -> tuple[str, ...]:
        """完全没有上报结果的套件（运行中断或未启动）。"""
        return tuple(
            tally.name for tally in self.suites if tally.executed == 0
        )

    @property
    def failed(self) -> tuple[str, ...]:
        """存在 not ok 用例的套件。"""
        return tuple(
            tally.name for tally in self.suites if tally.not_ok_count > 0
        )


def parse_tap(text: str, suites: tuple[tuple[str, int], ...]) -> TapReport:
    """把 TAP 文本聚合为报告。

    Args:
        text: kselftest 的 TAP 输出。
        suites: 预检得到的 (套件名, 期望用例数) 序列。

    Returns:
        TapReport：全局汇总与按套件明细。

    Raises:
        FarmConfigError: 输入类型或套件描述非法。
        FarmError: TAP 证据缺失计划行、上报未知套件或结果数超出预期。
    """
    if not isinstance(text, str):
        raise _cfg(f"text must be str, got {type(text).__name__}")
    expect = _expect(suites)
    plans: list[int] = []
    tally: dict[str, list[int]] = {name: [0, 0, 0] for name in expect}
    for line in text.splitlines():
        plan = _PLAN_RE.match(line)
        if plan is not None:
            plans.append(int(plan.group(2)))
            continue
        hit = _RESULT_RE.match(line)
        if hit is not None:
            _record(hit, tally)
    return _build(plans, expect, tally)


def read_tap(
    path: str | Path,
    suites: tuple[tuple[str, int], ...],
) -> TapReport:
    """读取 TAP 证据文件并聚合。

    Args:
        path: TAP 文件路径。
        suites: 预检得到的 (套件名, 期望用例数) 序列。

    Returns:
        TapReport 聚合结果。

    Raises:
        FarmConfigError: 路径类型非法或套件描述非法。
        FarmError: 文件不可读或 TAP 内容不合法。
    """
    if not isinstance(path, (str, Path)):
        raise _cfg(f"path must be str or Path, got {type(path).__name__}")
    here = Path(path)
    try:
        text = here.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FarmError(
            f"cannot read TAP evidence {here}: {exc}",
            AIRY_QEMU_ERROR,
        ) from exc
    return parse_tap(text, suites)


def collect_tap(raw: RawRun) -> TapReport:
    """聚合一次作业运行留下的 TAP 证据。

    Args:
        raw: 运行器返回的原始证据。

    Returns:
        TapReport 聚合结果。

    Raises:
        FarmConfigError: raw 类型非法。
        FarmError: TAP 文件不可读或内容不合法。
    """
    if not isinstance(raw, RawRun):
        raise _cfg(f"raw must be RawRun, got {type(raw).__name__}")
    return read_tap(raw.tap_path, raw.preflight.suite_tests)


def _expect(suites: tuple[tuple[str, int], ...]) -> dict[str, int]:
    """校验套件描述并转为有序映射。"""
    if not isinstance(suites, tuple) or not suites:
        raise _cfg("suites must be a non-empty tuple")
    expect: dict[str, int] = {}
    for item in suites:
        name, count = _suite_entry(item)
        if name in expect:
            raise _cfg(f"duplicate suite: {name!r}")
        expect[name] = count
    return expect


def _suite_entry(item: tuple[str, int]) -> tuple[str, int]:
    """校验单个 (套件名, 期望用例数) 条目。"""
    if not isinstance(item, tuple) or len(item) != 2:
        raise _cfg(f"invalid suite entry: {item!r}")
    name, count = item
    if not isinstance(name, str) or not name:
        raise _cfg(f"invalid suite entry: {item!r}")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise _cfg(f"invalid suite entry: {item!r}")
    return name, count


def _record(
    hit: re.Match[str],
    tally: dict[str, list[int]],
) -> None:
    """把一条结果行计入对应套件。"""
    body, directive = _split(hit.group("rest") or "")
    name = _suite_of(body)
    if name not in tally:
        raise FarmError(
            f"TAP result for unknown suite: {hit.group(0)!r}",
            AIRY_QEMU_ERROR,
        )
    slot = tally[name]
    if hit.group("status") == "not ok":
        slot[_BAD] += 1
    elif directive == "SKIP":
        slot[_SKIP] += 1
    else:
        slot[_OK] += 1


def _split(rest: str) -> tuple[str, str]:
    """剥离可选的前导 '- ' 与 TAP 指令，返回 (描述, 指令)。"""
    body = rest.strip()
    if body.startswith("- "):
        body = body[2:].strip()
    hit = _DIRECTIVE_RE.search(body)
    if hit is None:
        return body, ""
    return body[: hit.start()].strip(), hit.group("word").upper()


def _suite_of(body: str) -> str:
    """从 'selftests: <套件>: <测试>' 中提取套件名。"""
    hit = _SUITE_RE.match(body)
    return hit.group("suite") if hit is not None else ""


def _build(
    plans: list[int],
    expect: dict[str, int],
    tally: dict[str, list[int]],
) -> TapReport:
    """由计划行与计数构造自洽的报告。"""
    if not plans:
        raise FarmError(
            "TAP evidence has no plan line",
            AIRY_QEMU_ERROR,
        )
    total = sum(expect.values())
    declared = sum(plans)
    if declared > total:
        raise FarmError(
            f"TAP declares {declared} tests, preflight expected {total}",
            AIRY_QEMU_ERROR,
        )
    suites = tuple(
        _tally(name, expect[name], tally[name]) for name in expect
    )
    ok = sum(tally.ok_count for tally in suites)
    bad = sum(tally.not_ok_count for tally in suites)
    skip = sum(tally.skip_count for tally in suites)
    return TapReport(TapSummary(total, ok, bad, skip), suites)


def _tally(name: str, plan: int, slot: list[int]) -> SuiteTally:
    """构造单套件统计，并把越界证据转成 FarmError。"""
    ok, bad, skip = slot
    if ok + bad + skip > plan:
        raise FarmError(
            f"TAP reported {ok + bad + skip} results for suite {name!r}, "
            f"preflight expected {plan}",
            AIRY_QEMU_ERROR,
        )
    return SuiteTally(name, plan, ok, bad, skip)


def _need_count(value: object, name: str) -> None:
    """要求非负整数（bool 不算整数）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")


def _cfg(message: str) -> FarmConfigError:
    """构造配置错误。"""
    return FarmConfigError(message)
