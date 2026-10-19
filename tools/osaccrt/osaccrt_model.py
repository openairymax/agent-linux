# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_model — OS-ACC-RT 验收规程的数据模型与判定接口。

本模块是 OS-ACC-RT 的唯一事实源：判定四态、证据包六步、量化阈值、
证据条目与单实验结果均在此定义，构造即校验，拒绝不可追溯或不可复核
的输入。判定逻辑由后续分片实现 `Judge` 协议，本模块不承载任何裁决。

数据模型与 docs-closed 验收实验任务卡草案 §7 一致；证据条目的路径、
摘要与步骤约束来自该草案 §3.4 的证据包规范。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.4 证据包规范、§5 模块路径、§7 数据模型
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-01 接口与类型）

Example:
    >>> th = Threshold("boot.time", "OS-ACC-BOOT-010", "60", "s")
    >>> exp = ExpResult("exp-20260119-001", (th,))
    >>> exp.verdict is None
    True

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol

__all__ = [
    "EXP_ID_RE",
    "SHA256_RE",
    "Evidence",
    "ExpResult",
    "Judge",
    "Step",
    "Threshold",
    "Verdict",
    "need_text",
]

EXP_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


class Verdict(Enum):
    """实验判定四态，与 OS-ACC-RT §3.2 状态机一一对应。"""

    PASS = "PASS"
    FAIL = "FAIL"
    WAIVED = "WAIVED"
    BLOCKED = "BLOCKED"


class Step(Enum):
    """证据包六步，与证据目录的步骤文件一一对应（草案 §3.4）。"""

    PREPARE = "prepare"
    RUN = "run"
    CAPTURE = "capture"
    VERDICT = "verdict"
    REVIEW = "review"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class Threshold:
    """单条量化阈值，取值必须逐条引自 NFR/OS-ACC 原文。"""

    key: str
    source: str
    limit: str
    unit: str

    def __post_init__(self) -> None:
        need_text(self.key, "key")
        need_text(self.source, "source")
        need_text(self.limit, "limit")
        need_text(self.unit, "unit")


@dataclass(frozen=True)
class Evidence:
    """证据条目，落盘后由 sha256 链封口，目录 append-only。"""

    path: str
    sha256: str
    step: Step

    def __post_init__(self) -> None:
        _need_relpath(self.path)
        if not SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"sha256 must be 64 lowercase hex: {self.path!r}")
        if not isinstance(self.step, Step):
            raise ValueError(f"step must be Step, got {type(self.step).__name__}")


@dataclass
class ExpResult:
    """单实验结果；判定器只读 evidences，禁止旁路输入。"""

    exp_id: str
    thresholds: tuple[Threshold, ...]
    evidences: list[Evidence] = field(default_factory=list)
    verdict: Verdict | None = None
    waiver_sig: tuple[str, str] | None = None
    blocked_reason: str = ""

    def __post_init__(self) -> None:
        if not EXP_ID_RE.fullmatch(self.exp_id):
            raise ValueError(f"invalid exp_id: {self.exp_id!r}")
        _need_thresholds(self.thresholds)
        if not isinstance(self.evidences, list):
            raise ValueError("evidences must be a list")
        for item in self.evidences:
            if not isinstance(item, Evidence):
                raise ValueError("evidences must contain Evidence items")
        _need_verdict(self.verdict)
        _need_waiver(self)
        _need_blocked(self)

    @property
    def keys(self) -> tuple[str, ...]:
        """阈值键序列，供判定器逐条对账。"""
        return tuple(item.key for item in self.thresholds)

    @property
    def steps(self) -> tuple[Step, ...]:
        """已取证步骤集合，按出现顺序去重。"""
        return tuple(dict.fromkeys(item.step for item in self.evidences))


class Judge(Protocol):
    """判定器协议，实现位于 TV-01-04。"""

    def decide(self, result: ExpResult) -> Verdict:
        """按阈值表逐条对账，返回四态之一；证据缺失直接 FAIL。"""
        ...


def need_text(value: str, name: str) -> None:
    """校验必填文本非空且无首尾空白。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{name} must not be padded: {value!r}")


def _need_relpath(path: str) -> None:
    """校验证据路径为包内相对路径，禁止绝对路径与向上逃逸。"""
    need_text(path, "path")
    if path.startswith("/") or "\\" in path:
        raise ValueError(f"path must be relative POSIX: {path!r}")
    parts = path.split("/")
    if ".." in parts or "" in parts or "." in parts:
        raise ValueError(f"path must be normalized: {path!r}")


def _need_thresholds(items: tuple[Threshold, ...]) -> None:
    """校验阈值表非空、类型正确且键唯一。"""
    if not isinstance(items, tuple) or not items:
        raise ValueError("thresholds must be a non-empty tuple")
    for item in items:
        if not isinstance(item, Threshold):
            raise ValueError("thresholds must contain Threshold items")
    keys = [item.key for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate threshold keys: {keys}")


def _need_verdict(verdict: Verdict | None) -> None:
    """校验判定值属于四态或尚未判定。"""
    if verdict is not None and not isinstance(verdict, Verdict):
        raise ValueError(f"verdict must be Verdict or None: {verdict!r}")


def _need_waiver(result: ExpResult) -> None:
    """WAIVED 必须有双签且签署人互异，其余状态不得携带签署。"""
    sig = result.waiver_sig
    if result.verdict is not Verdict.WAIVED:
        if sig is not None:
            raise ValueError("waiver_sig only allowed with WAIVED")
        return
    if sig is None:
        raise ValueError("WAIVED requires waiver_sig")
    first, second = sig
    for signer in (first, second):
        need_text(signer, "waiver_sig")
        if "@" not in signer:
            raise ValueError(f"waiver_sig must be an email: {signer!r}")
    if first == second:
        raise ValueError("waiver_sig requires two distinct signers")


def _need_blocked(result: ExpResult) -> None:
    """BLOCKED 必须给出降级原因，其余状态不得携带原因。"""
    if result.verdict is Verdict.BLOCKED:
        need_text(result.blocked_reason, "blocked_reason")
    elif result.blocked_reason:
        raise ValueError("blocked_reason only allowed with BLOCKED")
