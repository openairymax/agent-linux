# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_evidence — 证据包清单与 sha256 链封口。

证据包是 OS-ACC-RT 唯一的裁决输入：每次实验在
``evidence/<日期>/<exp-id>/`` 下落盘六步产物，``manifest.json`` 登记
阈值、证据摘要与判定，``chain.sha256`` 以滚动摘要封口。本模块负责
清单的严格序列化、步骤落位校验与哈希链计算，不做任何判定。

链式摘要把前链头、实验编号与每条证据的步骤/路径/摘要依次喂入
sha256，任一字段被替换都会改变链头，从而阻断跨包拼接与静默改写。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-02 证据包 schema 与 sha256 链）

Example:
    >>> th = Threshold("boot.time", "OS-ACC-BOOT-010", "60", "s")
    >>> exp = ExpResult("exp-001", (th,))
    >>> exp.evidences.append(Evidence("step-02-run.log", "b" * 64, Step.RUN))
    >>> man = Manifest("2026-01-19", "qemu-x86_64", "a" * 64, exp)
    >>> len(seal(man))
    64

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from typing import Any, Final

from osaccrt_model import (
    EXP_ID_RE,
    SHA256_RE,
    Evidence,
    ExpResult,
    Step,
    Threshold,
    Verdict,
    need_text,
)

__all__ = [
    "Manifest",
    "STEP_LAYOUT",
    "chain_head",
    "dump",
    "load",
    "seal",
    "verify",
]

STEP_LAYOUT: Final[dict[Step, str]] = {
    Step.PREPARE: "step-01-prepare.json",
    Step.RUN: "step-02-run.log",
    Step.CAPTURE: "step-03-artifacts",
    Step.VERDICT: "step-04-verdict.json",
    Step.REVIEW: "step-05-review.json",
    Step.ARCHIVE: "chain.sha256",
}

_MAN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "exp_id",
        "date",
        "host",
        "image_sha256",
        "thresholds",
        "evidences",
        "verdict",
        "prev_chain_head",
        "waiver_sig",
        "blocked_reason",
        "supersedes",
    }
)
_MAN_OPT: Final[frozenset[str]] = frozenset(
    {"waiver_sig", "blocked_reason", "supersedes"}
)
_TH_KEYS: Final[tuple[str, ...]] = ("key", "source", "limit", "unit")
_EV_KEYS: Final[tuple[str, ...]] = ("path", "sha256", "step")


@dataclass(frozen=True)
class Manifest:
    """证据包清单；字段与草案 §3.4 一一对应，result 为唯一事实源。"""

    date: str
    host: str
    image_sha256: str
    result: ExpResult
    prev_chain_head: str = ""
    supersedes: str = ""

    def __post_init__(self) -> None:
        _check_date(self.date)
        need_text(self.host, "host")
        _check_sha(self.image_sha256, "image_sha256")
        _check_prev(self.prev_chain_head)
        _check_super(self.supersedes, self.result)
        _check_pack(self.result)

    @property
    def exp_id(self) -> str:
        """实验编号，与结果对象保持一致。"""
        return self.result.exp_id

    @property
    def chain_head(self) -> str:
        """按证据顺序滚动计算的链头；空证据集返回前链头。"""
        return chain_head(self.result.exp_id, self.result.evidences,
                          self.prev_chain_head)

    def to_dict(self) -> dict[str, Any]:
        """展开为 manifest.json 的字段集合，省略空的可选字段。"""
        data: dict[str, Any] = {
            "exp_id": self.result.exp_id,
            "date": self.date,
            "host": self.host,
            "image_sha256": self.image_sha256,
            "thresholds": [_th_dict(item) for item in self.result.thresholds],
            "evidences": [_ev_dict(item) for item in self.result.evidences],
            "verdict": _verdict_value(self.result.verdict),
            "prev_chain_head": self.prev_chain_head,
        }
        _add_optional(data, self)
        return data


def chain_head(exp_id: str, evidences: Sequence[Evidence], prev: str = "") -> str:
    """按证据顺序滚动计算链头；空证据集原样返回前链头。"""
    head = prev
    for item in evidences:
        head = _link(head, exp_id, item)
    return head


def seal(manifest: Manifest) -> str:
    """封口证据包并返回链头；空包拒绝封口。"""
    if not manifest.result.evidences:
        raise ValueError("cannot seal an empty evidence pack")
    return manifest.chain_head


def verify(manifest: Manifest, sealed: str) -> bool:
    """复核封口摘要与清单是否一致；空包恒不复核通过。"""
    if not manifest.result.evidences:
        return False
    return manifest.chain_head == sealed


def dump(manifest: Manifest) -> str:
    """序列化为规范化 JSON：键排序、原样 UTF-8、末尾换行。"""
    return json.dumps(
        manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"


def load(text: str) -> Manifest:
    """从 manifest.json 文本还原清单，拒绝未知或缺失字段。"""
    raw = _json_obj(text)
    _check_keys(raw)
    return Manifest(
        date=_str(raw["date"], "date"),
        host=_str(raw["host"], "host"),
        image_sha256=_str(raw["image_sha256"], "image_sha256"),
        result=_result(raw),
        prev_chain_head=_str(raw["prev_chain_head"], "prev_chain_head"),
        supersedes=_str(raw.get("supersedes", ""), "supersedes"),
    )


def _link(head: str, exp_id: str, item: Evidence) -> str:
    """把一条证据接到链上；字段以换行分隔，杜绝歧义拼接。"""
    payload = f"{head}\n{exp_id}\n{item.step.value}\n{item.path}\n{item.sha256}\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_date(value: str) -> None:
    """日期必须是零填充的 YYYY-MM-DD，且为真实日历日。"""
    try:
        parsed = _date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"date must be YYYY-MM-DD: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"date must be zero-padded: {value!r}")


def _check_sha(value: str, name: str) -> None:
    """摘要必须是 64 位小写十六进制。"""
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be 64 lowercase hex")


def _check_prev(value: str) -> None:
    """前链头为空（首包）或 64 位小写十六进制。"""
    if not value:
        return
    _check_sha(value, "prev_chain_head")


def _check_super(value: str, result: ExpResult) -> None:
    """supersedes 必须指向另一实验编号，禁止自指。"""
    if not value:
        return
    if not EXP_ID_RE.fullmatch(value):
        raise ValueError(f"invalid supersedes: {value!r}")
    if value == result.exp_id:
        raise ValueError("supersedes must reference another experiment")


def _check_pack(result: ExpResult) -> None:
    """校验证据步骤落位且路径唯一。"""
    if not isinstance(result, ExpResult):
        raise ValueError("result must be ExpResult")
    seen: set[str] = set()
    for item in result.evidences:
        _check_slot(item)
        if item.path in seen:
            raise ValueError(f"duplicate evidence path: {item.path!r}")
        seen.add(item.path)


def _check_slot(item: Evidence) -> None:
    """证据路径必须落在其步骤约定的文件或目录内。"""
    base = STEP_LAYOUT[item.step]
    if item.step is Step.ARCHIVE:
        raise ValueError("ARCHIVE is sealed by chain.sha256, not an evidence")
    if item.step is Step.CAPTURE:
        if item.path.startswith(base + "/") and item.path != base:
            return
    elif item.path == base:
        return
    raise ValueError(f"{item.step.value} evidence must live at {base}: {item.path!r}")


def _th_dict(item: Threshold) -> dict[str, str]:
    """阈值对象展开为 JSON 字段。"""
    return {name: getattr(item, name) for name in _TH_KEYS}


def _ev_dict(item: Evidence) -> dict[str, str]:
    """证据条目展开为 JSON 字段。"""
    return {
        "path": item.path,
        "sha256": item.sha256,
        "step": item.step.value,
    }


def _verdict_value(verdict: Verdict | None) -> str | None:
    """判定值展开为字符串或 JSON null。"""
    return None if verdict is None else verdict.value


def _add_optional(data: dict[str, Any], manifest: Manifest) -> None:
    """仅在字段有值时写入双签、降级原因与取代关系。"""
    sig = manifest.result.waiver_sig
    if sig is not None:
        data["waiver_sig"] = list(sig)
    if manifest.result.blocked_reason:
        data["blocked_reason"] = manifest.result.blocked_reason
    if manifest.supersedes:
        data["supersedes"] = manifest.supersedes


def _json_obj(text: str) -> dict[str, Any]:
    """解析 JSON 对象，语法或形状错误一律转换为 ValueError。"""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("manifest must be a JSON object")
    return raw


def _check_keys(raw: dict[str, Any]) -> None:
    """拒绝未知字段与缺失的必填字段。"""
    keys = set(raw)
    unknown = keys - _MAN_KEYS
    if unknown:
        raise ValueError(f"unknown manifest fields: {sorted(unknown)}")
    missing = (_MAN_KEYS - _MAN_OPT) - keys
    if missing:
        raise ValueError(f"missing manifest fields: {sorted(missing)}")


def _result(raw: dict[str, Any]) -> ExpResult:
    """还原结果对象，字段级校验交给模型构造器。"""
    return ExpResult(
        exp_id=_str(raw["exp_id"], "exp_id"),
        thresholds=_thresholds(raw["thresholds"]),
        evidences=_evidences(raw["evidences"]),
        verdict=_verdict(raw["verdict"]),
        waiver_sig=_waiver(raw.get("waiver_sig")),
        blocked_reason=_str(raw.get("blocked_reason", ""), "blocked_reason"),
    )


def _thresholds(items: Any) -> tuple[Threshold, ...]:
    """还原阈值表，条目必须为完整对象。"""
    if not isinstance(items, list):
        raise ValueError("thresholds must be a list")
    return tuple(_threshold(item) for item in items)


def _threshold(item: Any) -> Threshold:
    """还原单条阈值。"""
    if not isinstance(item, dict):
        raise ValueError("thresholds must contain objects")
    return Threshold(*(_str(item.get(name), name) for name in _TH_KEYS))


def _evidences(items: Any) -> list[Evidence]:
    """还原证据条目列表。"""
    if not isinstance(items, list):
        raise ValueError("evidences must be a list")
    return [_evidence(item) for item in items]


def _evidence(item: Any) -> Evidence:
    """还原单条证据。"""
    if not isinstance(item, dict):
        raise ValueError("evidences must contain objects")
    return Evidence(
        path=_str(item.get("path"), "path"),
        sha256=_str(item.get("sha256"), "sha256"),
        step=_step(item.get("step")),
    )


def _step(value: Any) -> Step:
    """还原步骤枚举。"""
    try:
        return Step(_str(value, "step"))
    except ValueError as exc:
        raise ValueError(f"invalid evidence step: {value!r}") from exc


def _verdict(value: Any) -> Verdict | None:
    """还原判定值；null 表示尚未判定。"""
    if value is None:
        return None
    try:
        return Verdict(_str(value, "verdict"))
    except ValueError as exc:
        raise ValueError(f"invalid verdict: {value!r}") from exc


def _waiver(value: Any) -> tuple[str, str] | None:
    """还原双签邮箱对。"""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("waiver_sig must be a two-element array")
    return (_str(value[0], "waiver_sig"), _str(value[1], "waiver_sig"))


def _str(value: Any, name: str) -> str:
    """要求字段为字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value
