# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/airy-flash 的数据类型、退出码与错误类型。

把「值对象」与「IO/渲染」分开，使预检、写入、回读三条路径都能在无 root、
无块设备的 CI 环境里用普通文件当靶子跑通单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ─── 退出码（与 airy-rollback 的分段保持一致：0/1/2 语义相同） ──────────────
EXIT_OK = 0        # 烧录完成且回读通过
EXIT_CHECK = 1     # 预检或校验不通过，未写入设备
EXIT_USAGE = 2     # 用法错误或运行环境不具备条件
EXIT_IO = 3        # 已开始写入，但写入/刷盘/回读失败

# 阶段状态：ok / fail / skip
OK = "ok"
FAIL = "fail"
SKIP = "skip"

# 回读校验强度
VERIFY_MODES = ("sample", "full", "none")


class FlashError(Exception):
    """带退出码的致命错误；由 main() 统一渲染后返回。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Check:
    """一条预检结论。skipped 用于「不适用」而非「通过」。"""

    name: str
    ok: bool
    detail: str
    skipped: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped": self.skipped,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Mount:
    """一行 /proc/mounts。"""

    source: str
    point: str


@dataclass(frozen=True)
class DeviceFacts:
    """目标设备的可判定事实，全部来自 sysfs/stat，不做写操作。"""

    path: Path
    kind: str                      # "block" | "file"
    size: int
    model: str = ""
    serial: str = ""
    tran: str = ""
    removable: bool = False
    read_only: bool = False
    mountpoints: tuple[str, ...] = ()
    system_disks: tuple[str, ...] = ()
    note: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_system_disk(self) -> bool:
        return self.kind == "block" and self.name in self.system_disks

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "bytes": self.size,
            "model": self.model,
            "serial": self.serial,
            "tran": self.tran,
            "removable": self.removable,
            "read_only": self.read_only,
            "mountpoints": list(self.mountpoints),
            "system_disks": list(self.system_disks),
            "note": self.note,
        }


@dataclass(frozen=True)
class ImageFacts:
    """镜像事实。digest 在「校验镜像」阶段才填充，避免预检阶段读满整盘。"""

    path: Path
    size: int
    digest: str = ""
    expected: str = ""
    sums_file: str = ""

    @property
    def digest_ok(self) -> bool:
        return bool(self.digest) and self.digest == self.expected

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "bytes": self.size,
            "sha256": self.digest,
            "sha256_expected": self.expected,
            "sha256_source": self.sums_file,
        }


@dataclass(frozen=True)
class Stage:
    """一个可展示的阶段结论。detail 是该阶段的人读摘要。"""

    index: int
    total: int
    name: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "total": self.total,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class Report:
    """一次运行的完整记录，是 --json 与 --report 的唯一来源。"""

    command: str
    result: str
    started: str
    finished: str = ""
    image: ImageFacts | None = None
    device: DeviceFacts | None = None
    checks: list[Check] = field(default_factory=list)
    stages: list[Stage] = field(default_factory=list)
    argv: list[str] = field(default_factory=list)
    verify: str = "sample"
    host: str = ""
    error: str = ""

    def as_dict(self, version: str) -> dict[str, object]:
        return {
            "tool": "airy-flash",
            "version": version,
            "command": self.command,
            "result": self.result,
            "host": self.host,
            "argv": list(self.argv),
            "verify": self.verify,
            "started": self.started,
            "finished": self.finished,
            "error": self.error,
            "image": self.image.as_dict() if self.image else None,
            "device": self.device.as_dict() if self.device else None,
            "checks": [c.as_dict() for c in self.checks],
            "stages": [s.as_dict() for s in self.stages],
        }
