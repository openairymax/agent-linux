# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""check_report — 契约检查工具的共享结果类型与报告输出。

六个契约检查工具（btf-integration、kbuild-version、ci-matrix、
defconfig-matrix、nightly、repo-scaffold）各自实现过同一组东西：一个
``name/passed/details`` 的三字段结果类型、一个清单错误异常、一段
「统计通过数 → 逐项打印 → 汇总一行」的报告函数，以及「schema 不通过
就短路」的前置判定。

风险不在重复本身，而在分歧：同一条判据在六处的输出格式稍有出入时，
CI 日志的读法就随工具而变，读者得先确认自己在看哪一个工具的输出。
报告格式属于对外契约，只能有一份。

本模块只收敛这层契约，不含任何工具的业务判定——每个工具的检查项仍留在
自己的目录里。

权威依据：
  docs-linux/50-engineering-standards/10-coding-style（命名与注释规范）
  tools/README.md（工具聚合清单）

Example:
    >>> print_report([CheckResult("kv_schema", True)], "KBUILD-VERSION",
    ...              True, "版本注入一致性报告", "1.2.0")
    KBUILD-VERSION: PASS (1/1 checks)

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

# 2. 可选依赖 PyYAML
#
# 六个工具的清单读取都要它，每个工具各写一遍「try 导入 / 缺则置 None」时，
# 探测口径就有六份；缺依赖时的提示词也会各自漂移。这里探测一次，工具按
# 自己的异常类型给提示，探测事实只来自本模块。
try:
    import yaml
except ImportError:  # pragma: no cover - 缺依赖由各工具的提示分支覆盖
    yaml = None

# 3. 公开导出
__all__ = [
    "CheckResult",
    "ManifestError",
    "lead",
    "print_report",
    "yaml",
]

# 4. 版式常量（报告宽度与详情缩进）
_RULE = "=" * 72
_THIN = "-" * 72
_BRIEF = 4
_FULL = 6


class ManifestError(Exception):
    """清单不可读取或 schema 非法。"""


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果。

    Attributes:
        name: 检查项名称，同时作为报告中的稳定标识。
        passed: 该项是否通过。
        details: 失败证据或补充说明；通过时为空串。
    """

    name: str
    passed: bool
    details: str = ""


def lead(first: CheckResult, rest: Iterable[CheckResult]) -> list[CheckResult]:
    """把前置检查置于首位；前置不通过即短路，不再执行后续检查。

    Args:
        first: 前置检查（通常是清单 schema）的结果。
        rest: 前置通过后才执行的检查结果。

    Returns:
        检查结果列表。
    """
    if not first.passed:
        return [first]
    return [first, *rest]


def print_report(
    results: Sequence[CheckResult],
    tag: str,
    check_mode: bool,
    title: str = "",
    version: str = "",
    tail: str = "",
) -> None:
    """打印检查报告。

    Args:
        results: 已执行的检查结果。
        tag: 汇总行的工具标识（大写，如 ``KBUILD-VERSION``）。
        check_mode: 为真时只输出汇总与失败证据，供 CI 消费。
        title: 完整报告标题；空串表示不输出报告头。
        version: 清单版本，随报告头输出；空串表示不输出该行。
        tail: 追加在版本行末尾的补充信息（如架构数）；空串表示不追加。
    """
    total = len(results)
    passed = _pass_count(results)
    failed = total - passed
    if check_mode:
        _brief(results, tag, passed, total, failed)
        return
    _verbose(results, title, version, tail, total, failed)


def _pass_count(results: Sequence[CheckResult]) -> int:
    """通过项数，与总数配对给出汇总分母。"""
    return sum(item.passed for item in results)


def _verbose(
    results: Sequence[CheckResult],
    title: str,
    version: str,
    tail: str,
    total: int,
    failed: int,
) -> None:
    """完整报告：报告头、逐项结果与汇总行。"""
    _banner(title, version, tail)
    for item in results:
        print(f"{'[+] PASS' if item.passed else '[-] FAIL'}: {item.name}")
        _detail(item.details, _FULL)
    print()
    print(_THIN)
    if failed == 0:
        print(f"RESULT: PASS — 全部 {total} 项检查通过")
    else:
        print(f"RESULT: FAIL — {failed} 项检查未通过")
    print(_THIN)


def _brief(
    results: Sequence[CheckResult],
    tag: str,
    passed: int,
    total: int,
    failed: int,
) -> None:
    """CI 精简格式：一行汇总加失败证据。"""
    if failed == 0:
        print(f"{tag}: PASS ({passed}/{total} checks)")
        return
    print(f"{tag}: FAIL ({passed}/{total} checks, {failed} failed)")
    for item in results:
        if item.passed:
            continue
        print(f"  FAIL: {item.name}")
        _detail(item.details, _BRIEF)


def _banner(title: str, version: str, tail: str) -> None:
    """完整报告的头部：标题与清单版本。"""
    if not title:
        return
    print(_RULE)
    print(title)
    if version:
        print(f"清单版本: {version}" + (f"    {tail}" if tail else ""))
    print(_RULE)


def _detail(text: str, indent: int) -> None:
    """逐行缩进输出证据；无证据时不输出空行。"""
    if not text:
        return
    pad = " " * indent
    for line in text.splitlines():
        print(f"{pad}{line}")
