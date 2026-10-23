# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_boot — image-smoke 串口启动日志解析。

本模块把 QEMU 串口日志转换为结构化的启动事实，是 V-02 执行器的取证
地基。解析层只做「行 → 标记」的确定性映射，不做任何裁决：通过与否由
判定域决定，本模块只保证事实可复现。

负向证据正则不在本模块里复制，统一由 ``distro/scripts/lib/verdict.env``
提供（唯一事实源），避免 bash 执行器与 Python 执行器出现双份真相。

标记文法由 distro 的装机与桌面探针产出，形如：

    [    5.104237] desktop_ready.sh[563]: AIRYMAX-BOOT-HEALTH failed=0
    AIRYMAX-BOOT-DEGRADED failed=<n> units=<unit list>
    AIRYMAX-DESKTOP-READY unit=<unit> class=<class> type=<type>
    AIRYMAX-DESKTOP-FAIL reason=session path=<path>
    AIRYMAX-DESKTOP-FAIL unit=<unit> wait=<seconds>
    AIRYMAX-SMOKE-PASS tap=<path> / AIRYMAX-SMOKE-FAIL reason=<why> tap=<path>
    AIRYMAX-TAP|<TAP 原文一行>
    AIRYMAX-BOOT-CHAIN|<critical-chain 原文一行>

TAP 与关键链都是「原文转发」：探针在 guest 侧不改写 systemd 的输出格式，
只加可剥离的前缀，宿主侧因此能与 `systemd-analyze` 的既有文法保持同步。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §1.4 语言路由、§2 E1-02 双固件冷启动、§3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9 模块 V（V-02 启动冒烟执行器）

Example:
    >>> log = parse_text("[    7.1] d.sh[5]: AIRYMAX-DESKTOP-READY unit=lightdm")
    >>> log.secs(TAG_READY)
    7.1
    >>> log.smoke() is None
    True

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_ENV",
    "TAG_CHAIN",
    "TAG_DEGRADED",
    "TAG_DIAG",
    "TAG_DFAIL",
    "TAG_HEALTH",
    "TAG_READY",
    "TAG_SFAIL",
    "TAG_SPASS",
    "BootLog",
    "Mark",
    "load_env",
    "parse_text",
    "read_log",
]

TAG_HEALTH: Final[str] = "AIRYMAX-BOOT-HEALTH"
TAG_DEGRADED: Final[str] = "AIRYMAX-BOOT-DEGRADED"
TAG_READY: Final[str] = "AIRYMAX-DESKTOP-READY"
TAG_DIAG: Final[str] = "AIRYMAX-DESKTOP-DIAG"
TAG_DFAIL: Final[str] = "AIRYMAX-DESKTOP-FAIL"
TAG_SPASS: Final[str] = "AIRYMAX-SMOKE-PASS"
TAG_SFAIL: Final[str] = "AIRYMAX-SMOKE-FAIL"
TAG_CHAIN: Final[str] = "AIRYMAX-BOOT-CHAIN"

DEFAULT_ENV: Final[Path] = (
    Path(__file__).resolve().parents[2] / "distro/scripts/lib/verdict.env"
)

_TAP_LINE: Final[str] = "AIRYMAX-TAP|"
_CHAIN_LINE: Final[str] = "AIRYMAX-BOOT-CHAIN|"
_PREFIX: Final[str] = "AIRYMAX-"
_KERNEL: Final[re.Pattern[str]] = re.compile(r"^\[\s*(\d+\.\d+)\]\s*")
_PROG: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.\-]+\[\d+\]:\s*")
_PAIR: Final[re.Pattern[str]] = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")
_ENV: Final[re.Pattern[str]] = re.compile(r"^([A-Z][A-Z0-9_]*)='([^']*)'\s*$")


@dataclass(frozen=True)
class Mark:
    """一条 AIRYMAX 标记：标签、内核实秒与 k=v 字段。"""

    tag: str
    secs: float
    fields: tuple[tuple[str, str], ...]

    def get(self, key: str, default: str = "") -> str:
        """按字段名取值，缺省返回 default。"""
        for name, value in self.fields:
            if name == key:
                return value
        return default


@dataclass(frozen=True)
class BootLog:
    """一次串口会话的解析结果，只承载事实，不做判定。"""

    marks: tuple[Mark, ...]
    tap: tuple[str, ...]
    chain: tuple[str, ...]
    raw: tuple[str, ...]

    def find(self, tag: str) -> tuple[Mark, ...]:
        """返回该标签的全部出现，保持串口顺序。"""
        return tuple(mark for mark in self.marks if mark.tag == tag)

    def last(self, tag: str) -> Mark | None:
        """返回该标签最后一次出现，未出现为 None。"""
        for mark in reversed(self.marks):
            if mark.tag == tag:
                return mark
        return None

    def secs(self, tag: str) -> float | None:
        """返回该标签最后出现时的内核实秒，无时间戳为 None。"""
        mark = self.last(tag)
        if mark is None or mark.secs < 0:
            return None
        return mark.secs

    def smoke(self) -> bool | None:
        """会话自报的冒烟结论：True 通过、False 失败、None 未上报。"""
        if self.last(TAG_SFAIL) is not None:
            return False
        if self.last(TAG_SPASS) is not None:
            return True
        return None

    def fault(self, pattern: str) -> str | None:
        """返回第一条命中负向证据正则的原始行，未命中为 None。"""
        for line in self.raw:
            if re.search(pattern, line):
                return line
        return None


def parse_text(text: str) -> BootLog:
    """把串口全文解析为标记序列、TAP 原文、关键链原文与原始行。"""
    marks: list[Mark] = []
    tap: list[str] = []
    chain: list[str] = []
    raw = tuple(text.splitlines())
    for line in raw:
        secs, rest = _split(line)
        if rest.startswith(_TAP_LINE):
            tap.append(rest[len(_TAP_LINE) :])
            continue
        if rest.startswith(_CHAIN_LINE):
            chain.append(rest[len(_CHAIN_LINE) :])
            continue
        mark = _mark(secs, rest)
        if mark is not None:
            marks.append(mark)
    return BootLog(tuple(marks), tuple(tap), tuple(chain), raw)


def read_log(path: Path | str) -> BootLog:
    """读取串口日志文件并解析，不可解码字节按替换字符保留。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_text(text)


def load_env(path: Path | str = DEFAULT_ENV) -> dict[str, str]:
    """读取 verdict.env 的正则集合，缺 BOOT_FAIL 视为事实源损坏。"""
    table: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _ENV.match(line)
        if match is not None:
            table[match.group(1)] = match.group(2)
    if "BOOT_FAIL" not in table:
        raise ValueError(f"verdict env lacks BOOT_FAIL: {path}")
    return table


def _split(line: str) -> tuple[float, str]:
    """剥离内核实秒与程序名两类前缀，缺失秒数时返回 -1.0。"""
    secs = -1.0
    rest = line
    stamp = _KERNEL.match(rest)
    if stamp is not None:
        secs = float(stamp.group(1))
        rest = rest[stamp.end() :]
    prog = _PROG.match(rest)
    if prog is not None:
        rest = rest[prog.end() :]
    return secs, rest


def _mark(secs: float, rest: str) -> Mark | None:
    """识别 AIRYMAX 标记行，非标记行返回 None。"""
    if not rest.startswith(_PREFIX):
        return None
    head, _, tail = rest.partition(" ")
    return Mark(head, secs, tuple(_PAIR.findall(tail)))
