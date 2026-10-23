# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_smoke — 冷启动冒烟判定（NFR-P-005）。

把镜像清单的逐固件结论与串口会话的冒烟上报、桌面就绪时刻、负向证据行
折叠为单条可复核事实，再按阈值表的 ``boot.cold`` 比较表达式给出四态
结论。耗时优先取串口 ``AIRYMAX-DESKTOP-READY`` 的内核实秒，缺失时间戳
时退回清单实测的启动秒数；两者都没有即视为证据缺口。

结论只依赖已取证事实：会话未上报冒烟标记时不给 PASS，而是 BLOCKED，
避免把"没测到"当成"通过"。阈值来自 ``specs/nfr.toml``，本模块不自带
任何数字。``boot.edge``（边缘 <10s）与 ``boot.service``（关键服务 <5s）
分别由边缘硬件与服务级实验承担，不复用 x86_64 串口证据充数。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §2 E1-02 双固件冷启动、§5.2 TV-02-06
    docs/docs-linux/00-requirements/03-non-functional-requirements.md
        NFR-P-005 冷启动时延

Example:
    >>> smoke = judge(image, {"bios": log}, table)
    >>> smoke.verdict
    <Verdict.PASS: 'PASS'>

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from osaccrt_boot import TAG_READY, BootLog, load_env
from osaccrt_img import Boot, Image
from osaccrt_judge import CAPTURE_ROOT
from osaccrt_model import Verdict
from osaccrt_spec import SpecTable

__all__ = [
    "COLD_KEY",
    "Sample",
    "Smoke",
    "artifact",
    "judge",
    "payload",
]

COLD_KEY: Final[str] = "boot.cold"
_LIMIT: Final[re.Pattern[str]] = re.compile(
    r"(<=|>=|<|>|=)\s*(-?\d+(?:\.\d+)?)"
)
_OPS: Final[Mapping[str, Callable[[float, float], bool]]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "=": operator.eq,
}
_RANK: Final[tuple[Verdict, ...]] = (Verdict.FAIL, Verdict.BLOCKED)


@dataclass(frozen=True)
class Sample:
    """单固件冒烟事实：清单结论、会话上报、故障行与阈值。"""

    fw: str
    booted: bool
    boot_s: float | None
    ready_s: float | None
    smoke: bool | None
    fault: str
    limit: str

    @property
    def secs(self) -> float | None:
        """冷启动耗时：优先串口就绪时刻，缺时间戳时退回清单实测。"""
        return self.ready_s if self.ready_s is not None else self.boot_s

    @property
    def over(self) -> bool:
        """耗时可测且不满足阈值；不可测不算超限。"""
        return self.secs is not None and not _meet(self.limit, self.secs)

    @property
    def broken(self) -> bool:
        """已取证且证据显示失败。"""
        return any((
            bool(self.fault),
            self.smoke is False,
            not self.booted,
            self.over,
        ))

    @property
    def gap(self) -> bool:
        """证据缺口：未上报冒烟结论或没有任何耗时来源。"""
        return any((self.smoke is None, self.secs is None))

    @property
    def state(self) -> Verdict:
        """单固件结论：失败优先于缺口，缺口优先于通过。"""
        if self.broken:
            return Verdict.FAIL
        return Verdict.BLOCKED if self.gap else Verdict.PASS


@dataclass(frozen=True)
class Smoke:
    """一次冒烟判定的全部事实与结论。"""

    key: str
    limit: str
    samples: tuple[Sample, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.samples, tuple) or not self.samples:
            raise ValueError("samples must be a non-empty tuple")
        if not all(isinstance(item, Sample) for item in self.samples):
            raise ValueError("samples must contain Sample items")

    @property
    def verdict(self) -> Verdict:
        """任一固件失败即失败；否则任一缺口即阻塞；全绿才通过。"""
        states = {item.state for item in self.samples}
        for state in _RANK:
            if state in states:
                return state
        return Verdict.PASS

    @property
    def gaps(self) -> tuple[str, ...]:
        """结论为阻塞的固件名，供失败分类直接引用。"""
        return tuple(
            item.fw for item in self.samples
            if item.state is Verdict.BLOCKED
        )


def judge(image: Image, logs: Mapping[str, BootLog],
          table: SpecTable, fail_re: str | None = None) -> Smoke:
    """按阈值表的冷启动门限判定每个固件的冒烟结论。"""
    limit = table.get(COLD_KEY).limit
    pattern = load_env()["BOOT_FAIL"] if fail_re is None else fail_re
    samples = tuple(
        _sample(fw, image, logs, limit, pattern)
        for fw in _firmwares(image)
    )
    return Smoke(COLD_KEY, limit, samples)


def artifact(key: str = COLD_KEY) -> str:
    """取证产物在证据包内的相对路径。"""
    return f"{CAPTURE_ROOT}/{key}.json"


def payload(smoke: Smoke) -> bytes:
    """把判定事实序列化为取证产物，字段稳定便于复核与重放。"""
    data = {
        "key": smoke.key,
        "limit": smoke.limit,
        "verdict": smoke.verdict.value,
        "gaps": list(smoke.gaps),
        "samples": [_row(item) for item in smoke.samples],
    }
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _firmwares(image: Image) -> tuple[str, ...]:
    """清单中的固件序列，去重并保持首次出现顺序。"""
    return tuple(dict.fromkeys(item.firmware for item in image.boots))


def _sample(fw: str, image: Image, logs: Mapping[str, BootLog],
            limit: str, pattern: str) -> Sample:
    """取一个固件的清单行与会话，折叠为单条事实。"""
    return _fold(fw, image.boots_of(fw), logs.get(fw), limit, pattern)


def _fold(fw: str, rows: tuple[Boot, ...], log: BootLog | None,
          limit: str, pattern: str) -> Sample:
    """折叠清单行与串口会话；会话缺失时事实留空而不臆测。"""
    return Sample(
        fw=fw,
        booted=bool(rows) and all(item.passed for item in rows),
        boot_s=_boot_s(rows),
        ready_s=None if log is None else log.secs(TAG_READY),
        smoke=None if log is None else log.smoke(),
        fault=_fault(log, pattern),
        limit=limit,
    )


def _boot_s(rows: tuple[Boot, ...]) -> float | None:
    """清单实测启动耗时取最差一次，缺测返回 None。"""
    seen = [item.secs for item in rows if item.secs is not None]
    return max(seen) if seen else None


def _fault(log: BootLog | None, pattern: str) -> str:
    """负向证据行原文；无会话时为空串。"""
    return "" if log is None else (log.fault(pattern) or "")


def _meet(limit: str, value: float) -> bool:
    """按阈值表的比较表达式判定实测值。"""
    match = _LIMIT.fullmatch(limit)
    if match is None:
        raise ValueError(f"malformed limit: {limit!r}")
    return _OPS[match.group(1)](value, float(match.group(2)))


def _row(item: Sample) -> dict[str, object]:
    """单固件事实的取证字段。"""
    return {
        "firmware": item.fw,
        "booted": item.booted,
        "boot_s": item.boot_s,
        "ready_s": item.ready_s,
        "smoke": item.smoke,
        "fault": item.fault,
        "state": item.state.value,
    }
