# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_waiver — FAIL 经双签转为 WAIVED 的复核工作流。

WAIVED 是判定状态机中最容易被滥用的出口：它把一次未达标的实验记为
有条件接受，因此必须同时满足三条硬约束——只有 FAIL 可以转入、必须
有两位互异签署人、必须给出可复核的豁免依据。本模块把依据、双签与
状态迁移收敛到一处，并把复核结论序列化为 ``step-05-review.json``，
随证据包一同封口。

豁免依据仅两类：等价替代证据必须指向包内已取证条目；影响面经评审
可接受则不得附带证据路径。任何越界输入都在构造期拒绝，避免不可追溯
的理由进入台账。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.1 六步规程、§3.2 判定状态机、§3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-08 WAIVED 双签工作流）

Example:
    >>> sig = ("a@example.com", "b@example.com")
    >>> item = Waiver(Basis.ACCEPTED, "仅影响日志格式", "", sig)
    >>> apply(failed, item).verdict
    <Verdict.WAIVED: 'WAIVED'>

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from osaccrt_evidence import STEP_LAYOUT, Manifest
from osaccrt_model import (
    ExpResult,
    Step,
    Verdict,
    need_line,
    need_relpath,
    need_signers,
)

__all__ = ["Basis", "Waiver", "apply", "load", "record", "verify"]

_REQ: Final[frozenset[str]] = frozenset({"basis", "reason", "signers"})
_EVID: Final[str] = "evidence"
_REVIEW: Final[str] = STEP_LAYOUT[Step.REVIEW]


class Basis(Enum):
    """豁免依据：等价替代证据或影响面经评审可接受。"""

    EQUIVALENT = "equivalent"
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class Waiver:
    """一次双签豁免：依据、理由、可选的等价证据与两位签署人。"""

    basis: Basis
    reason: str
    evidence: str
    signers: tuple[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.basis, Basis):
            raise ValueError(f"basis must be Basis: {self.basis!r}")
        need_line(self.reason, "reason")
        need_signers(self.signers)
        object.__setattr__(self, "signers", tuple(self.signers))
        _check_basis(self.basis, self.evidence)


def apply(result: ExpResult, waiver: Waiver) -> ExpResult:
    """把 FAIL 结果转为 WAIVED；返回新对象，不修改原结果。"""
    if not isinstance(result, ExpResult):
        raise TypeError("result must be ExpResult")
    if not isinstance(waiver, Waiver):
        raise TypeError("waiver must be Waiver")
    if result.verdict is not Verdict.FAIL:
        raise ValueError("only FAIL can be waived")
    _need_pack(result, waiver)
    return ExpResult(
        result.exp_id,
        result.thresholds,
        list(result.evidences),
        Verdict.WAIVED,
        waiver.signers,
    )


def record(waiver: Waiver) -> bytes:
    """序列化复核结论，作为 step-05-review.json 的落盘内容。"""
    if not isinstance(waiver, Waiver):
        raise TypeError("waiver must be Waiver")
    data: dict[str, Any] = {
        "basis": waiver.basis.value,
        "reason": waiver.reason,
        "signers": list(waiver.signers),
    }
    if waiver.evidence:
        data[_EVID] = waiver.evidence
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def load(raw: str | bytes) -> Waiver:
    """从复核记录还原豁免结论，拒绝未知或缺失字段。"""
    data = _json(raw)
    _check_keys(data)
    return Waiver(
        _basis(data["basis"]),
        _text(data["reason"], "reason"),
        _text(data.get(_EVID, ""), _EVID),
        _signers(data["signers"]),
    )


def verify(manifest: Manifest, waiver: Waiver) -> bool:
    """核对已封口清单与复核结论是否互证。"""
    if not isinstance(manifest, Manifest):
        raise TypeError("manifest must be Manifest")
    if not isinstance(waiver, Waiver):
        raise TypeError("waiver must be Waiver")
    result = manifest.result
    if result.verdict is not Verdict.WAIVED:
        return False
    if result.waiver_sig != waiver.signers:
        return False
    paths = {item.path for item in result.evidences}
    if _REVIEW not in paths:
        return False
    if waiver.basis is Basis.ACCEPTED:
        return True
    return waiver.evidence in paths


def _check_basis(basis: Basis, evidence: str) -> None:
    """等价证据必填且为包内路径，评审可接受不得附带证据。"""
    if basis is Basis.EQUIVALENT:
        if not evidence:
            raise ValueError("equivalent waiver requires evidence")
        need_relpath(evidence)
        return
    if evidence:
        raise ValueError("accepted waiver must not carry evidence")


def _need_pack(result: ExpResult, waiver: Waiver) -> None:
    """等价证据必须已在包内取证，杜绝引用包外材料。"""
    if waiver.basis is not Basis.EQUIVALENT:
        return
    paths = {item.path for item in result.evidences}
    if waiver.evidence not in paths:
        raise ValueError(f"waiver evidence not in pack: {waiver.evidence!r}")


def _json(raw: str | bytes) -> dict[str, Any]:
    """解析复核记录，语法或形状错误一律转换为 ValueError。"""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"review is not UTF-8: {exc}") from exc
    if not isinstance(raw, str):
        raise TypeError("review must be str or bytes")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"review is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("review must be a JSON object")
    return data


def _check_keys(data: dict[str, Any]) -> None:
    """拒绝未知字段与缺失的必填字段。"""
    keys = set(data)
    unknown = keys - (_REQ | {_EVID})
    if unknown:
        raise ValueError(f"unknown review fields: {sorted(unknown)}")
    missing = _REQ - keys
    if missing:
        raise ValueError(f"missing review fields: {sorted(missing)}")


def _basis(value: Any) -> Basis:
    """还原豁免依据枚举。"""
    try:
        return Basis(_text(value, "basis"))
    except ValueError as exc:
        raise ValueError(f"invalid waiver basis: {value!r}") from exc


def _signers(value: Any) -> tuple[str, str]:
    """还原双签邮箱对。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("review signers must be a two-element array")
    return (_text(value[0], "signers"), _text(value[1], "signers"))


def _text(value: Any, name: str) -> str:
    """要求字段为字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value
