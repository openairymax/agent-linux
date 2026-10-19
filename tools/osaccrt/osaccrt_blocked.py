# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_blocked — 实机缺失的诚实降级（BLOCKED）登记与核对。

BLOCKED 既不是失败也不是豁免：它把「环境不具备、实验无法执行」如实
记入竣工台账，不参与通过率，也不允许用替代数据冒充实测结论。因此
入口必须收敛——只允许准备步证据，降级范围固定为任务卡 §3.3 预登记
的三项，任何未登记的平台、实验或理由都拒绝入账。

登记表是本模块的唯一事实源：序列化与还原都只能在该表允许的取值内
往返，封口后的清单可据此反查降级是否诚实。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.1 六步规程、§3.2 判定状态机、§3.3 BLOCKED 预登记
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-09 BLOCKED 诚实降级登记）

Example:
    >>> item = lookup("sw_64")
    >>> item.reason
    'sw_64 裸机缺失：改用 QEMU 等效镜像冒烟'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from osaccrt_codec import as_text, as_texts, check_keys, read_obj
from osaccrt_evidence import STEP_LAYOUT, Manifest
from osaccrt_model import (
    EXP_ID_RE,
    ExpResult,
    Step,
    Verdict,
    need_line,
)

__all__ = [
    "Entry",
    "REGISTRY",
    "apply",
    "ledger",
    "load",
    "lookup",
    "record",
    "verify",
]

_KEY_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*")
_PREP: Final[str] = STEP_LAYOUT[Step.PREPARE]
_REQ: Final[frozenset[str]] = frozenset(
    {"fallback", "exps", "key", "platform"}
)


def _need_key(key: str) -> None:
    """校验登记键为字面量风格的稳定标识。"""
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise ValueError(f"invalid blocked key: {key!r}")


def _need_exps(items: Sequence[str]) -> tuple[str, ...]:
    """校验受影响实验为非空、去重且合法的编号元组。"""
    if isinstance(items, str) or not isinstance(items, Sequence):
        raise ValueError("exps must be a non-empty sequence")
    exps = tuple(items)
    if not exps:
        raise ValueError("exps must not be empty")
    for item in exps:
        if not isinstance(item, str) or not EXP_ID_RE.fullmatch(item):
            raise ValueError(f"invalid exp id: {item!r}")
    if len(exps) != len(set(exps)):
        raise ValueError(f"duplicate exp ids: {list(exps)}")
    return exps


@dataclass(frozen=True)
class Entry:
    """一条预登记降级项：缺失平台、受影响实验与允许的降级方式。"""

    key: str
    platform: str
    exps: tuple[str, ...]
    fallback: str

    def __post_init__(self) -> None:
        _need_key(self.key)
        need_line(self.platform, "platform")
        need_line(self.fallback, "fallback")
        object.__setattr__(self, "exps", _need_exps(self.exps))

    @property
    def reason(self) -> str:
        """清单中的降级原因：写明缺失机型与允许的降级方式。"""
        return f"{self.platform}缺失：{self.fallback}"


REGISTRY: Final[tuple[Entry, ...]] = (
    Entry(
        "sw_64",
        "sw_64 裸机",
        ("E1-02", "E1-04", "E1-05"),
        "改用 QEMU 等效镜像冒烟",
    ),
    Entry(
        "loongarch",
        "loongarch 裸机",
        ("E1-02", "E1-04"),
        "改用 QEMU 等效镜像冒烟",
    ),
    Entry(
        "m2-smp",
        "M2 SMP 大规模多核实机",
        ("E3-03", "E3-04"),
        "改用中小规模 QEMU SMP 实测并附外推说明",
    ),
)


def lookup(key: str) -> Entry:
    """按登记键取预登记项；未登记键一律拒绝。"""
    _need_key(key)
    for item in REGISTRY:
        if item.key == key:
            return item
    raise ValueError(f"unregistered blocked entry: {key!r}")


def apply(result: ExpResult, key: str) -> ExpResult:
    """把仅含准备证据的结果登记为 BLOCKED；返回新对象。"""
    if not isinstance(result, ExpResult):
        raise TypeError("result must be ExpResult")
    entry = lookup(key)
    if result.exp_id not in entry.exps:
        raise ValueError(f"{entry.key} does not block {result.exp_id}")
    if set(result.steps) != {Step.PREPARE}:
        raise ValueError("BLOCKED is only reachable from PREPARE")
    return ExpResult(
        result.exp_id,
        result.thresholds,
        list(result.evidences),
        Verdict.BLOCKED,
        None,
        entry.reason,
    )


def record(entry: Entry) -> bytes:
    """序列化降级登记，作为 step-01-prepare.json 的落盘内容。"""
    if not isinstance(entry, Entry):
        raise TypeError("entry must be Entry")
    data: dict[str, Any] = {
        "exps": list(entry.exps),
        "fallback": entry.fallback,
        "key": entry.key,
        "platform": entry.platform,
    }
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def load(raw: str | bytes) -> Entry:
    """从准备记录还原登记项，只接受与预登记一致的取值。"""
    data = read_obj(raw, "prepare")
    check_keys(data, _REQ, what="prepare")
    entry = lookup(as_text(data["key"], "key"))
    bad = _diff(entry, data)
    if bad:
        raise ValueError(f"{bad} mismatch: {entry.key}")
    return entry


def verify(manifest: Manifest) -> bool:
    """核对已封口清单是否为诚实降级：登记在册且只有准备证据。"""
    if not isinstance(manifest, Manifest):
        raise TypeError("manifest must be Manifest")
    result = manifest.result
    if result.verdict is not Verdict.BLOCKED:
        return False
    entry = _match(result.blocked_reason)
    if entry is None or result.exp_id not in entry.exps:
        return False
    paths = {item.path for item in result.evidences}
    return paths == {_PREP} and set(result.steps) == {Step.PREPARE}


def ledger(results: Sequence[ExpResult]) -> tuple[tuple[str, str], ...]:
    """汇总 BLOCKED 台账行（实验编号, 登记键），未登记即拒绝。"""
    rows: list[tuple[str, str]] = []
    for result in results:
        if not isinstance(result, ExpResult):
            raise TypeError("results must contain ExpResult")
        if result.verdict is not Verdict.BLOCKED:
            continue
        entry = _match(result.blocked_reason)
        if entry is None or result.exp_id not in entry.exps:
            raise ValueError(f"unregistered blocked: {result.exp_id}")
        rows.append((result.exp_id, entry.key))
    return tuple(sorted(rows))


def _diff(entry: Entry, data: dict[str, Any]) -> str:
    """返回首个与预登记不一致的字段名；全部一致时返回空串。"""
    pairs = (
        ("platform", entry.platform, as_text(data["platform"], "platform")),
        ("exps", entry.exps, as_texts(data["exps"], "exps")),
        ("fallback", entry.fallback, as_text(data["fallback"], "fallback")),
    )
    for name, want, got in pairs:
        if got != want:
            return name
    return ""


def _match(reason: str) -> Entry | None:
    """按降级原因反查登记项；未登记的原因返回 None。"""
    for item in REGISTRY:
        if item.reason == reason:
            return item
    return None

