# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_bootchain — systemd 冷启动关键链解析（NFR-P-005）。

桌面就绪探针在 guest 侧把 ``systemd-analyze critical-chain --no-pager``
的原文逐行以 ``AIRYMAX-BOOT-CHAIN|`` 转发到串口。本模块把这份原文还原
为单元链：每个单元何时达成（``@``）、自身启动耗时多少（``+``），并以
链上自身耗时最长的一环作为关键服务，对账 ``boot.service``（< 5 s）。

关键链只承担服务级门限。全系统 ``boot.cold`` 由用户可见的桌面就绪时刻
判定（osaccrt_smoke），同一条 NFR 不在两个模块里各判一次，否则同一份
启动会得出两个互相打架的结论。链上没有任何单元耗时即给 BLOCKED 而不是
PASS——没测到不等于达标。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §2 E1-03 冷启动关键链、§5.2 TV-02-05
    docs/docs-linux/00-requirements/03-non-functional-requirements.md
        NFR-P-005 冷启动时延（关键服务启动 < 5s）

Example:
    >>> trail = parse("bios", ("graphical.target @7.1s",), "< 5")
    >>> trail.total_s
    7.1

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from osaccrt_boot import BootLog
from osaccrt_judge import CAPTURE_ROOT
from osaccrt_model import Verdict
from osaccrt_spec import SpecTable, meet

__all__ = [
    "CHAIN_KEY",
    "Chain",
    "Link",
    "Trail",
    "artifact",
    "judge",
    "parse",
    "payload",
]

CHAIN_KEY: Final[str] = "boot.service"
_RANK: Final[tuple[Verdict, ...]] = (Verdict.FAIL, Verdict.BLOCKED)
_GLYPH: Final[str] = " │├└─┌┐┘┬┴┼"
_TOK: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)(us|ms|min|h|s)")
_UNIT: Final[Mapping[str, float]] = {
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "min": 60.0,
    "h": 3600.0,
}


@dataclass(frozen=True)
class Link:
    """链上一环：单元名、达成时刻与自身启动耗时。"""

    unit: str
    at_s: float
    dur_s: float | None


@dataclass(frozen=True)
class Trail:
    """单固件的关键链：原文还原出的整条链与判定门限。"""

    fw: str
    links: tuple[Link, ...]
    limit: str

    @property
    def root(self) -> str:
        """链首单元，通常是 graphical.target。"""
        return self.links[0].unit if self.links else ""

    @property
    def total_s(self) -> float | None:
        """链首单元的达成时刻，即 systemd 记下的整机启动耗时。"""
        return self.links[0].at_s if self.links else None

    @property
    def slow(self) -> Link | None:
        """关键服务：链上自身耗时最长的一环，无耗时为 None。"""
        seen = [item for item in self.links if item.dur_s is not None]
        if not seen:
            return None
        return max(seen, key=lambda item: item.dur_s or 0.0)

    @property
    def slow_s(self) -> float | None:
        """关键服务自身启动耗时。"""
        link = self.slow
        return None if link is None else link.dur_s

    @property
    def state(self) -> Verdict:
        """无整机耗时或无单元耗时即阻塞；关键服务超限即失败。"""
        secs = self.slow_s
        if self.total_s is None or secs is None:
            return Verdict.BLOCKED
        return Verdict.PASS if meet(self.limit, secs) else Verdict.FAIL


@dataclass(frozen=True)
class Chain:
    """一次关键链判定的全部事实与结论。"""

    key: str
    limit: str
    trails: tuple[Trail, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.trails, tuple) or not self.trails:
            raise ValueError("trails must be a non-empty tuple")
        if not all(isinstance(item, Trail) for item in self.trails):
            raise ValueError("trails must contain Trail items")

    @property
    def verdict(self) -> Verdict:
        """任一固件失败即失败；否则任一缺口即阻塞；全绿才通过。"""
        states = {item.state for item in self.trails}
        for state in _RANK:
            if state in states:
                return state
        return Verdict.PASS

    @property
    def gaps(self) -> tuple[str, ...]:
        """结论为阻塞的固件名，供失败分类直接引用。"""
        return tuple(
            item.fw for item in self.trails
            if item.state is Verdict.BLOCKED
        )


def judge(logs: Mapping[str, BootLog], table: SpecTable) -> Chain:
    """按阈值表的服务级门限判定每个固件的关键链。"""
    limit = table.get(CHAIN_KEY).limit
    trails = tuple(parse(fw, logs[fw].chain, limit) for fw in sorted(logs))
    return Chain(CHAIN_KEY, limit, trails)


def parse(fw: str, lines: Sequence[str], limit: str) -> Trail:
    """把一条关键链原文还原为单元序列；无可用行时事实留空。"""
    links = tuple(
        item for item in (_link(line) for line in lines) if item is not None
    )
    return Trail(fw, links, limit)


def artifact(key: str = CHAIN_KEY) -> str:
    """取证产物在证据包内的相对路径。"""
    return f"{CAPTURE_ROOT}/{key}.json"


def payload(chain: Chain) -> bytes:
    """把关键链事实序列化为取证产物，保留原文还原出的每一环。"""
    data = {
        "key": chain.key,
        "limit": chain.limit,
        "verdict": chain.verdict.value,
        "gaps": list(chain.gaps),
        "trails": [_row(item) for item in chain.trails],
    }
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _link(line: str) -> Link | None:
    """解析单行 ``unit @time [+duration]``，非单元行返回 None。"""
    head, sep, tail = line.partition("@")
    if not sep:
        return None
    words = head.split()
    if not words:
        return None
    unit = words[-1].lstrip(_GLYPH)
    at_s, dur_s = _times(tail)
    if not unit or at_s is None:
        return None
    return Link(unit, at_s, dur_s)


def _times(tail: str) -> tuple[float | None, float | None]:
    """拆分达成时刻与自身耗时；systemd 的时间量词可能含空格。"""
    at_txt = ""
    dur_txt: str | None = None
    for word in tail.split():
        if word.startswith("+"):
            dur_txt = word[1:]
        elif dur_txt is None:
            at_txt += word
        else:
            dur_txt += word
    return _secs(at_txt), None if dur_txt is None else _secs(dur_txt)


def _secs(text: str) -> float | None:
    """把 systemd 的时间量词累加为秒，无匹配返回 None。"""
    parts = _TOK.findall(text)
    if not parts:
        return None
    return sum(float(num) * _UNIT[unit] for num, unit in parts)


def _row(trail: Trail) -> dict[str, object]:
    """单固件关键链的取证字段。"""
    link = trail.slow
    return {
        "firmware": trail.fw,
        "root": trail.root,
        "total_s": trail.total_s,
        "key_unit": "" if link is None else link.unit,
        "key_s": trail.slow_s,
        "state": trail.state.value,
        "links": [_cell(item) for item in trail.links],
    }


def _cell(link: Link) -> dict[str, object]:
    """单环的取证字段。"""
    return {"unit": link.unit, "at_s": link.at_s, "dur_s": link.dur_s}
