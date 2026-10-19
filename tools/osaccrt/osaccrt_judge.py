# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_judge — OS-ACC-RT 判定状态机。

判定器只读实验结果对象，按证据包推导 PASS/FAIL/WAIVED/BLOCKED 四态：

- 证据完备（准备、执行、取证三步齐全）且每条阈值都有同名取证产物，
  才允许 PASS；
- 证据缺失、阈值无产物或判定声明与证据不符，一律 FAIL；
- WAIVED 除证据完备外必须有双签邮箱（签名约束由数据模型强制）；
- BLOCKED 是准备步的唯一出口：只允许准备证据与降级原因同时存在，
  一旦出现执行或取证证据即视为降级不诚实，改判 FAIL。

阈值对账以 ``step-03-artifacts/<key>`` 文件或目录为准，键即产物名。
数值比较属于各实验域的职责（如启动冒烟、长稳判定），本模块只做
状态机与可追溯性裁决，不引入任何旁路输入。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.1 六步规程、§3.2 判定状态机、§3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-04 判定状态机）

Example:
    >>> import osaccrt_model as om
    >>> th = om.Threshold("boot.time", "NFR-P-005", "30", "s")
    >>> PackJudge().decide(om.ExpResult("exp-001", (th,))).value
    'FAIL'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from osaccrt_model import ExpResult, Step, Verdict

__all__ = ["CAPTURE_ROOT", "PackJudge", "missing_keys"]

CAPTURE_ROOT: Final[str] = "step-03-artifacts"
_REQUIRED: Final[tuple[Step, ...]] = (Step.PREPARE, Step.RUN, Step.CAPTURE)


class PackJudge:
    """证据包判定状态机，实现 `osaccrt_model.Judge` 协议。"""

    def decide(self, result: ExpResult) -> Verdict:
        """返回四态判定；未声明时按证据推导，声明不实一律降为 FAIL。"""
        if not isinstance(result, ExpResult):
            raise TypeError("result must be ExpResult")
        claim = result.verdict
        if claim is None:
            return Verdict.PASS if _pass_ok(result) else Verdict.FAIL
        if claim is Verdict.FAIL:
            return Verdict.FAIL
        check = _ALLOW[claim]
        return claim if check(result) else Verdict.FAIL


def missing_keys(result: ExpResult) -> tuple[str, ...]:
    """返回缺少取证产物的阈值键，顺序与阈值表一致。"""
    paths = tuple(
        item.path for item in result.evidences if item.step is Step.CAPTURE
    )
    return tuple(
        key for key in result.keys if not any(_covers(path, key) for path in paths)
    )


def _covers(path: str, key: str) -> bool:
    """阈值键对应同名文件、同名 JSON 或同名目录下的产物。"""
    stem = f"{CAPTURE_ROOT}/{key}"
    if path in (stem, f"{stem}.json"):
        return True
    return path.startswith(f"{stem}/")


def _complete(result: ExpResult) -> bool:
    """准备、执行、取证三步必须齐全。"""
    steps = set(result.steps)
    return all(step in steps for step in _REQUIRED)


def _pass_ok(result: ExpResult) -> bool:
    """PASS 要求证据完备且逐阈值对账无缺口。"""
    return _complete(result) and not missing_keys(result)


def _waived_ok(result: ExpResult) -> bool:
    """WAIVED 必须先有完整证据，双签约束由数据模型保证。"""
    return result.waiver_sig is not None and _complete(result)


def _blocked_ok(result: ExpResult) -> bool:
    """BLOCKED 只允许准备证据，出现后续步骤即降级不诚实。"""
    if not result.blocked_reason:
        return False
    return set(result.steps) == {Step.PREPARE}


_ALLOW: Final[dict[Verdict, Callable[[ExpResult], bool]]] = {
    Verdict.PASS: _pass_ok,
    Verdict.WAIVED: _waived_ok,
    Verdict.BLOCKED: _blocked_ok,
}
