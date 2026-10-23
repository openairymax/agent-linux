# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_img — 镜像清单解析与制品摘要校验。

镜像的构建与装机由 distro 的 ``scripts/install-test.sh`` 承担（唯一事实
源），它把制品身份与逐固件结论写入 ``install-manifest.txt``。本模块只
消费该清单，把文本转换为不可变的镜像身份与启动结论，并按需重算制品
sha256 做对账，不重复实现任何 QEMU 或装机逻辑。

清单文法（install-test.sh 产出）：

    # AirymaxOS install test manifest
    iso <sha256> <file name>
    arch=x86_64
    firmware=both
    runs=1
    run bios run=1 install=pass
    run uefi run=1 boot=pass boot_s=14
    result=pass

``run`` 行的键值对按 (固件, 轮次) 归并，因此同一轮的分段结论可以分散
在多行上；未知标量键被忽略，以便清单向前扩展而不破坏解析。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §2 E1-01 镜像摘要校验、E1-02 双固件冷启动
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9 模块 V（V-02 制品定位与校验）

Example:
    >>> img = parse_text("iso " + "0" * 64 + " a.iso\\nresult=pass")
    >>> img.sha256 == "0" * 64
    True

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from osaccrt_model import SHA256_RE, need_text

__all__ = [
    "Boot",
    "Image",
    "digest",
    "parse_text",
    "read_manifest",
]

_PAIR: Final[re.Pattern[str]] = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")
_CHUNK: Final[int] = 1 << 20
_REQUIRED: Final[tuple[str, ...]] = ("arch", "firmware", "runs", "result")


@dataclass(frozen=True)
class Boot:
    """单固件单轮的装机与冷启动结论。"""

    firmware: str
    index: int
    install: str
    boot: str
    secs: float | None

    @property
    def passed(self) -> bool:
        """装机与冷启动同时通过才算该轮通过。"""
        return self.install == "pass" and self.boot == "pass"


@dataclass(frozen=True)
class Image:
    """镜像身份与逐固件启动结论。"""

    sha256: str
    name: str
    arch: str
    firmware: str
    runs: int
    result: str
    boots: tuple[Boot, ...]

    def boots_of(self, firmware: str) -> tuple[Boot, ...]:
        """返回指定固件的全部轮次，保持清单顺序。"""
        return tuple(row for row in self.boots if row.firmware == firmware)

    @property
    def passed(self) -> bool:
        """清单自报通过且每一轮都通过。"""
        return self.result == "pass" and all(row.passed for row in self.boots)


def parse_text(text: str) -> Image:
    """把清单文本解析为镜像身份与逐固件结论。"""
    iso: tuple[str, str] | None = None
    meta: dict[str, str] = {}
    rows: dict[tuple[str, int], dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, tail = line.partition(" ")
        if head == "iso":
            iso = _iso(tail)
        elif head == "run":
            _row(tail, rows)
        else:
            _meta(line, meta)
    return _image(iso, meta, rows)


def read_manifest(path: Path | str) -> Image:
    """读取并解析清单文件。"""
    return parse_text(Path(path).read_text(encoding="utf-8"))


def digest(path: Path | str) -> str:
    """按 1 MiB 分块计算文件 sha256，避免整文件驻留内存。"""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _iso(tail: str) -> tuple[str, str]:
    """解析 iso 行：摘要必须是小写 64 位十六进制。"""
    parts = tail.split()
    if len(parts) != 2:
        raise ValueError(f"iso line must hold sha256 and name: {tail!r}")
    sha, name = parts
    if not SHA256_RE.fullmatch(sha):
        raise ValueError(f"iso sha256 must be 64 lowercase hex: {sha!r}")
    need_text(name, "iso name")
    return sha, name


def _row(tail: str, rows: dict[tuple[str, int], dict[str, str]]) -> None:
    """把 run 行的键值对按 (固件, 轮次) 归并。"""
    firmware, _, rest = tail.partition(" ")
    need_text(firmware, "firmware")
    pairs = dict(_PAIR.findall(rest))
    raw = pairs.pop("run", "1")
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"run index must be positive: {raw!r}")
    rows.setdefault((firmware, int(raw)), {}).update(pairs)


def _meta(line: str, meta: dict[str, str]) -> None:
    """记录清单级的标量键值，无等号的行视为格式错误。"""
    key, sep, value = line.partition("=")
    if not sep or not key or not value:
        raise ValueError(f"manifest line is not key=value: {line!r}")
    meta[key] = value


def _image(
    iso: tuple[str, str] | None,
    meta: dict[str, str],
    rows: dict[tuple[str, int], dict[str, str]],
) -> Image:
    """装配镜像对象，缺项与非法取值一律拒绝。"""
    if iso is None:
        raise ValueError("manifest lacks the iso line")
    absent = [key for key in _REQUIRED if key not in meta]
    if absent:
        raise ValueError(f"manifest lacks fields: {absent}")
    runs = meta["runs"]
    if not runs.isdigit() or int(runs) < 1:
        raise ValueError(f"runs must be positive: {runs!r}")
    if not rows:
        raise ValueError("manifest lacks run rows")
    boots = tuple(
        _boot(firmware, index, item)
        for (firmware, index), item in sorted(rows.items())
    )
    return Image(
        sha256=iso[0],
        name=iso[1],
        arch=meta["arch"],
        firmware=meta["firmware"],
        runs=int(runs),
        result=meta["result"],
        boots=boots,
    )


def _boot(firmware: str, index: int, item: dict[str, str]) -> Boot:
    """装配单轮结论，缺 install/boot 视为清单不完整。"""
    missing = [key for key in ("install", "boot") if key not in item]
    if missing:
        raise ValueError(f"run {firmware}/{index} lacks fields: {missing}")
    raw = item.get("boot_s", "")
    secs = float(raw) if raw else None
    if secs is not None and secs < 0:
        raise ValueError(f"boot_s must not be negative: {raw!r}")
    return Boot(firmware, index, item["install"], item["boot"], secs)
