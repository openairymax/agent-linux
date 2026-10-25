#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
airy_flash.py — AirymaxOS 烧录工具

把「下载好的 ISO → U 盘/整块设备」这件事收敛成一条可复现、可取证的命令：

  airy-flash plan   --image X.iso --device /dev/sdX     # 只预检，不碰设备
  airy-flash write  --image X.iso --device /dev/sdX --yes
  airy-flash verify --image X.iso --device /dev/sdX      # 只回读比对
  airy-flash check                                       # 环境自检

设计要点：
  * 预检与写入分离。plan 不打开目标设备写句柄，可先对着实机跑到全绿。
  * 目标设备用 sysfs 判定：块设备类型、容量、只读、挂载点、是否系统盘。
    「系统盘」按 /proc/mounts 里 /、/boot、/boot/efi、/usr、/var 的来源
    反查父磁盘，而不是靠名字猜 sdX。
  * 校验和先于写入：镜像摘要必须在写入前核对发布 .sha256，缺失则拒绝，
    除非显式 --allow-unverified。
  * 写入后回读比对：sample 抽头部/中部/尾部三窗，full 全量，none 跳过。
  * 输出分「映像/目标/预检/阶段/结果/下一步」六段，TTY 下带进度条与吞吐，
    非 TTY 退化为逐行百分比；--json 输出同一份 Report 的机器可读形式。
  * 结束打印 AIRY-FLASH-PASS/FAIL 标记行，便于串口日志与 CI 采集。

Exit codes:
    0 — 成功
    1 — 预检或校验不通过（未写入设备）
    2 — 用法错误或运行环境不具备条件
    3 — 已开始写入，但写入/刷盘/回读失败
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
import socket
import stat
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from unicodedata import east_asian_width

from flash_types import (
    EXIT_CHECK,
    EXIT_IO,
    EXIT_OK,
    EXIT_USAGE,
    FAIL,
    OK,
    SKIP,
    VERIFY_MODES,
    Check,
    DeviceFacts,
    FlashError,
    ImageFacts,
    Mount,
    Report,
    Stage,
)

__all__ = [
    "CHUNK",
    "SAMPLE_WINDOW",
    "TOOL_VERSION",
    "build_parser",
    "execute",
    "human_bytes",
    "human_duration",
    "main",
    "parse_sums",
    "probe_device",
    "probe_image",
    "read_mounts",
    "readback",
    "render",
    "resolve_expected",
    "sample_ranges",
    "sha256_file",
    "system_disks",
    "write_image",
]

TOOL_VERSION = "0.0.3"

CHUNK = 4 << 20          # 单次读写块，4 MiB：对 U 盘与页缓存都友好
SAMPLE_WINDOW = 16 << 20  # 抽检窗口
CRITICAL_MOUNTS = ("/", "/boot", "/boot/efi", "/usr", "/var")
BLOCK_SECTOR = 512


# ─── 人读格式化 ──────────────────────────────────────────────────────────────


def human_bytes(value: float) -> str:
    """字节数 → "3.43 GiB" 这类固定三位有效数字的短表示。"""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"  # pragma: no cover - 循环已覆盖


def human_duration(seconds: float) -> str:
    """秒 → "42s" / "1m06s" / "1h02m"。"""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def short_digest(digest: str, head: int = 8, tail: int = 8) -> str:
    if len(digest) <= head + tail:
        return digest
    return f"{digest[:head]}…{digest[-tail:]}"


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_width(text: str) -> int:
    """终端显示宽度：CJK 全角字符占两列。"""
    return sum(2 if east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


# ─── 校验和 ─────────────────────────────────────────────────────────────────


def parse_sums(text: str) -> dict[str, str]:
    """解析 sha256sum 格式（兼容二进制标记 `*` 与 `./` 前缀）。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        digest = digest.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            continue
        name = name.strip().lstrip("*").strip()
        while name.startswith("./"):
            name = name[2:]
        out[Path(name).name] = digest
    return out


def resolve_expected(
    image: Path, *, sha256: str = "", sums: str = ""
) -> tuple[str, str]:
    """返回 (期望摘要, 来源说明)；来源为空表示没有可用校验和。"""
    if sha256:
        value = sha256.strip().lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise FlashError(EXIT_USAGE, f"--sha256 不是 64 位十六进制: {sha256}")
        return value, "--sha256"

    candidates: list[Path] = []
    if sums:
        candidates.append(Path(sums))
    candidates.extend(
        [image.with_suffix(image.suffix + ".sha256"), image.parent / "SHA256SUMS"]
    )
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        table = parse_sums(text)
        if image.name in table:
            return table[image.name], str(candidate)
        if sums and len(table) == 1:
            return next(iter(table.values())), str(candidate)
        if sums:
            raise FlashError(
                EXIT_CHECK, f"{candidate} 中没有 {image.name} 的摘要"
            )
    return "", ""


def sha256_file(
    path: Path,
    *,
    chunk: int = CHUNK,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress is not None:
                progress(done, total)
    return digest.hexdigest()


# ─── 设备探测 ───────────────────────────────────────────────────────────────


def read_mounts(path: Path = Path("/proc/mounts")) -> list[Mount]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    mounts: list[Mount] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            mounts.append(Mount(parts[0], parts[1]))
    return mounts


def _read_attr(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _parent_disk(name: str, sys_block: Path) -> str:
    """sdb1 → sdb；nvme0n1p2 → nvme0n1；整盘原样返回。

    sysfs 里分区是 .../block/sdb/sdb1，realpath 的父目录名才是整盘；
    直接取 realpath 的 basename 会得到 sdb1，判定系统盘时漏掉。
    """
    entry = Path(sys_block) / name
    if (entry / "partition").exists():
        return Path(os.path.realpath(entry)).parent.name
    return name


def system_disks(
    mounts: Iterable[Mount], sys_block: Path = Path("/sys/class/block")
) -> tuple[str, ...]:
    """承载关键挂载点的父磁盘名集合。"""
    names: set[str] = set()
    for mount in mounts:
        if mount.point not in CRITICAL_MOUNTS or not mount.source.startswith("/dev/"):
            continue
        # /dev/mapper/vg-root 这类符号链接先落到 /dev/dm-N，再反查 sysfs。
        name = os.path.basename(os.path.realpath(mount.source))
        names.add(_parent_disk(name, sys_block))
    return tuple(sorted(names))


def probe_image(path: Path) -> ImageFacts:
    target = Path(os.path.realpath(path))
    try:
        info = target.stat()
    except OSError as exc:
        raise FlashError(EXIT_USAGE, f"镜像不可读: {path} ({exc.strerror})") from exc
    if not stat.S_ISREG(info.st_mode):
        raise FlashError(EXIT_USAGE, f"镜像不是普通文件: {target}")
    if info.st_size == 0:
        raise FlashError(EXIT_CHECK, f"镜像是空文件: {target}")
    return ImageFacts(path=target, size=info.st_size)


def probe_device(
    path: Path,
    *,
    mounts: Sequence[Mount] | None = None,
    sys_block: Path = Path("/sys/class/block"),
    allow_file: bool = False,
) -> DeviceFacts:
    """只读取证，不打开写句柄。allow_file 用于测试与虚拟机镜像。"""
    target = Path(os.path.realpath(path))
    if not target.exists():
        raise FlashError(EXIT_CHECK, f"目标不存在: {target}")

    try:
        info = target.stat()
    except OSError as exc:  # pragma: no cover - exists() 已过
        raise FlashError(EXIT_CHECK, f"无法 stat 目标: {target}") from exc

    table = list(mounts) if mounts is not None else read_mounts()
    prefix = target.as_posix()

    if stat.S_ISBLK(info.st_mode):
        entry = Path(sys_block) / target.name
        sectors = _read_attr(entry / "size")
        size = int(sectors) * BLOCK_SECTOR if sectors.isdigit() else 0
        points = tuple(
            sorted(
                {
                    m.point
                    for m in table
                    if m.source == prefix or m.source.startswith(prefix)
                }
            )
        )
        return DeviceFacts(
            path=target,
            kind="block",
            size=size,
            model=_read_attr(entry / "device" / "model"),
            serial=_read_attr(entry / "device" / "serial"),
            tran=_read_attr(entry / "device" / "transport")
            or _read_attr(entry / "device" / "subsystem"),
            removable=_read_attr(entry / "removable") == "1",
            read_only=_read_attr(entry / "ro") == "1",
            mountpoints=points,
            system_disks=system_disks(table, sys_block),
        )

    if stat.S_ISREG(info.st_mode):
        if not allow_file:
            raise FlashError(
                EXIT_CHECK,
                f"{target} 是普通文件；写镜像到文件需显式 --allow-file",
            )
        return DeviceFacts(
            path=target,
            kind="file",
            size=info.st_size,
            note="普通文件靶子（测试/虚拟机镜像）",
        )

    raise FlashError(EXIT_CHECK, f"目标既不是块设备也不是普通文件: {target}")


# ─── 预检 ───────────────────────────────────────────────────────────────────


def preflight(
    image: ImageFacts,
    device: DeviceFacts,
    *,
    expected: str,
    source: str,
    allow_unverified: bool = False,
) -> list[Check]:
    """写入前必须全部通过的判定；任一条 FAIL 即拒绝写设备。"""
    checks: list[Check] = []

    if device.kind == "block":
        checks.append(Check("目标是块设备", True, str(device.path)))
    else:
        checks.append(
            Check("目标是块设备", True, f"{device.path}（普通文件，测试模式）", skipped=True)
        )

    checks.append(
        Check(
            "非系统盘",
            not device.is_system_disk,
            (
                f"承载 {', '.join(CRITICAL_MOUNTS)} 的磁盘: "
                f"{', '.join(device.system_disks) or '（未识别）'}"
                if device.is_system_disk
                else f"系统盘: {', '.join(device.system_disks) or '（未识别）'}"
            ),
        )
    )

    checks.append(
        Check(
            "未挂载",
            not device.mountpoints,
            (
                f"已挂载于 {', '.join(device.mountpoints)}，请先 umount"
                if device.mountpoints
                else "无挂载点"
            ),
        )
    )

    if device.kind == "block":
        checks.append(
            Check(
                "非只读",
                not device.read_only,
                "设备处于只读状态（写保护开关或 ro 属性）"
                if device.read_only
                else "可写",
            )
        )
    else:
        checks.append(Check("非只读", True, "普通文件读写", skipped=True))

    enough = device.size >= image.size
    checks.append(
        Check(
            "容量充足",
            enough,
            f"{human_bytes(device.size)} {'≥' if enough else '<'} "
            f"{human_bytes(image.size)}",
        )
    )

    if expected:
        checks.append(Check("校验和来源", True, source))
    elif allow_unverified:
        checks.append(
            Check("校验和来源", True, "未提供发布摘要（--allow-unverified）", skipped=True)
        )
    else:
        checks.append(
            Check(
                "校验和来源",
                False,
                "缺少发布 .sha256；用 --sha256/--sums 指定，或显式 --allow-unverified",
            )
        )

    return checks


# ─── 写入与回读 ─────────────────────────────────────────────────────────────


def write_image(
    image: Path,
    device: Path,
    *,
    chunk: int = CHUNK,
    progress: Callable[[int, int], None] | None = None,
    file_target: bool = False,
) -> int:
    """顺序写镜像到设备，返回字节数。调用方负责 fsync 阶段。"""
    total = image.stat().st_size
    written = 0
    with open(image, "rb") as src, open(device, "r+b", buffering=0) as dst:
        try:
            while True:
                block = src.read(chunk)
                if not block:
                    break
                offset = 0
                while offset < len(block):
                    offset += dst.write(block[offset:])
                written += len(block)
                if progress is not None:
                    progress(written, total)
            if file_target and total:
                os.ftruncate(dst.fileno(), total)
        except OSError as exc:
            raise FlashError(
                EXIT_IO,
                f"写入失败于偏移 {human_bytes(written)}: {exc.strerror or exc}",
            ) from exc
    return written


def flush_device(device: Path) -> None:
    """独立成阶段：U 盘的 fsync 常常比写入本身还久。"""
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise FlashError(EXIT_IO, "刷盘需要写权限，请以 root 运行") from exc
        raise FlashError(EXIT_IO, f"刷盘失败: {exc.strerror or exc}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise FlashError(EXIT_IO, f"fsync 失败: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)


def sample_ranges(size: int, window: int = SAMPLE_WINDOW) -> list[tuple[int, int]]:
    """抽检区间：头/中/尾三窗；镜像不足三窗时退化为全量。"""
    if size <= window * 3:
        return [(0, size)]
    middle = size // 2 - window // 2
    return [(0, window), (middle, middle + window), (size - window, size)]


def readback(
    image: Path,
    device: Path,
    mode: str,
    *,
    chunk: int = CHUNK,
    window: int = SAMPLE_WINDOW,
) -> tuple[bool, str, list[tuple[int, int]]]:
    """回读比对。返回 (一致, 说明, 比对区间)。"""
    size = image.stat().st_size
    ranges = [(0, size)] if mode == "full" else sample_ranges(size, window)
    try:
        with open(image, "rb") as src, open(device, "rb", buffering=0) as dst:
            for start, end in ranges:
                src.seek(start)
                dst.seek(start)
                remaining = end - start
                cursor = start
                while remaining > 0:
                    want = min(chunk, remaining)
                    left = src.read(want)
                    right = dst.read(want)
                    if len(left) != len(right):
                        return False, f"偏移 {cursor} 读取长度不一致", ranges
                    if left != right:
                        index = next(
                            (i for i, (a, b) in enumerate(zip(left, right)) if a != b),
                            0,
                        )
                        return (
                            False,
                            f"偏移 {cursor + index} 内容不一致",
                            ranges,
                        )
                    cursor += len(left)
                    remaining -= len(left)
    except OSError as exc:
        raise FlashError(EXIT_IO, f"回读失败: {exc.strerror or exc}") from exc
    return True, f"比对 {len(ranges)} 段 / {human_bytes(sum(e - s for s, e in ranges))}", ranges


# ─── 渲染 ───────────────────────────────────────────────────────────────────


class Renderer:
    """人读输出。TTY 走进度条，非 TTY 走逐行里程碑，两者都无 ANSI 污染日志。"""

    WIDTH = 68

    def __init__(self, stream=None, tty: bool | None = None) -> None:
        self.stream = stream or sys.stdout
        self.tty = self.stream.isatty() if tty is None else tty
        self._bar_open = False
        self._last_bucket = -1
        self._current = Stage(0, 0, "", OK)

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def rule(self, char: str = "-") -> None:
        self._write(char * self.WIDTH + "\n")

    def title(self, text: str) -> None:
        self._write(f"{text}\n")
        self.rule("=")

    def field(self, label: str, value: str, extra: str = "") -> None:
        # 标签按 6 列补齐后接一个空格，故续行缩进固定 7 列。
        indent = " " * 7
        self._write(f"{pad(label, 6)} {value}\n")
        if extra:
            self._write(f"{indent}{extra}\n")

    def section(self, name: str) -> None:
        self._write(f"\n{name}\n")

    def check(self, check: Check) -> None:
        tag = "[SKIP]" if check.skipped else ("[ OK ]" if check.ok else "[FAIL]")
        self._write(f"  {tag} {pad(check.name, 16)} {check.detail}\n")

    def stage_start(self, stage: Stage) -> None:
        """只登记当前阶段：进度行与结束行各自自带前缀，避免半行残留。"""
        self._current = stage
        self._last_bucket = -1

    def stage_progress(self, done: int, total: int, rate: float, eta: float) -> None:
        ratio = done / total if total else 1.0
        stage = self._current
        head = f"{stage.index}/{stage.total} {pad(stage.name, 10)}"
        if self.tty:
            width = 24
            filled = max(0, min(width, int(ratio * width)))
            bar = "#" * filled + "." * (width - filled)
            self._write(
                f"\r  [ .. ] {head} {bar} {ratio * 100:3.0f}% "
                f"{human_bytes(done)}/{human_bytes(total)} "
                f"{human_bytes(rate)}/s ETA {human_duration(eta)}"
            )
            self._bar_open = True
            return
        bucket = int(ratio * 10)
        if bucket != self._last_bucket:
            self._last_bucket = bucket
            self._write(
                f"  [ .. ] {head} {bucket * 10:3d}% {human_bytes(done)} "
                f"{human_bytes(rate)}/s ETA {human_duration(eta)}\n"
            )

    def stage_end(self, stage: Stage) -> None:
        tag = "[ OK ]" if stage.status == OK else ("[SKIP]" if stage.status == SKIP else "[FAIL]")
        prefix = "\r" if self._bar_open else ""
        self._write(
            f"{prefix}  {tag} {stage.index}/{stage.total} {pad(stage.name, 10)} {stage.detail}\n"
        )
        self._bar_open = False
        self._last_bucket = -1

    def note(self, text: str) -> None:
        self._write(f"  {text}\n")

    def stages_header(self) -> None:
        self.section("阶段")

    def checks_header(self) -> None:
        self.section("预检")

    def summary(self, report: Report, elapsed: float, report_path: str = "") -> None:
        self.section("结果")
        mark = "成功" if report.result == "pass" else "失败"
        self._write(f"  结果    {mark}    用时 {human_duration(elapsed)}\n")
        if report.error:
            self._write(f"  原因    {report.error}\n")
        if report_path:
            self._write(f"  报告    {report_path}\n")

    def marker(self, report: Report) -> None:
        # 失败时可能还没算完摘要，用期望值兜底，便于把日志对回发布物。
        digest = ""
        if report.image:
            digest = report.image.digest or report.image.expected
        device = report.device.path if report.device else ""
        status = "PASS" if report.result == "pass" else "FAIL"
        self._write(
            f"AIRY-FLASH-{status} image={report.image.path if report.image else ''} "
            f"device={device} sha256={digest}\n"
        )

    def next_steps(self) -> None:
        self.section("下一步")
        for line in (
            "1. 安全弹出 U 盘，插入目标机，开机按 F12/F11/Esc 选择该 U 盘",
            "2. UEFI 固件请选带 UEFI 前缀的条目；BIOS 模式直接选 USB 设备",
            '3. 装机后核对：hostname=airymaxos，uname -r 以 "-alk" 结尾',
            "4. 核对登录策略：airymax 本地/SSH 免密，root 口令 00000000",
        ):
            self._write(f"  {line}\n")


def _meter(total: int, started: float) -> Callable[[int, int], tuple[float, float]]:
    """返回 (已完成, 总量) → (瞬时速率, 剩余秒数) 的换算器。"""

    def compute(done: int, _total: int = total) -> tuple[float, float]:
        elapsed = max(time.monotonic() - started, 1e-6)
        rate = done / elapsed
        remaining = (total - done) / rate if rate > 0 else 0.0
        return rate, remaining

    return compute


# ─── 编排 ───────────────────────────────────────────────────────────────────


def execute(args: argparse.Namespace) -> tuple[Report, int]:
    report = Report(
        command=args.command,
        result="fail",
        started=now_iso(),
        argv=list(sys.argv) if args.argv is None else list(args.argv),
        verify=args.verify,
        host=socket.gethostname(),
    )
    renderer = Renderer(
        stream=io.StringIO() if args.json else None,
        tty=False if (args.no_progress or args.json) else None,
    )
    started = time.monotonic()

    image = probe_image(Path(args.image))
    expected, source = resolve_expected(
        image.path, sha256=args.sha256, sums=args.sums
    )
    image = ImageFacts(
        path=image.path,
        size=image.size,
        expected=expected,
        sums_file=source,
    )

    device = probe_device(
        Path(args.device),
        mounts=read_mounts(Path(args.mounts)) if args.mounts else None,
        sys_block=Path(args.sys_block),
        allow_file=args.allow_file,
    )
    report.image, report.device = image, device

    if device.kind == "block" and args.command != "plan" and os.geteuid() != 0:
        raise FlashError(EXIT_USAGE, "写块设备需要 root：请用 sudo 重新执行")

    report.checks = preflight(
        image,
        device,
        expected=expected,
        source=source,
        allow_unverified=args.allow_unverified,
    )

    write_mode = args.command == "write"
    if args.command == "plan":
        stages_total = 1
    else:
        stages_total = 4 if write_mode else 2

    if write_mode and device.kind == "block" and not args.yes:
        raise FlashError(
            EXIT_USAGE,
            f"将覆盖 {device.path}（{human_bytes(device.size)}）上的全部数据；确认请加 --yes",
        )

    renderer.title(f"AirymaxOS 烧录工具 {TOOL_VERSION}  ({args.command})")
    renderer.field(
        "映像",
        str(image.path),
        f"{human_bytes(image.size)}   sha256 {short_digest(expected) if expected else '（未提供）'}",
    )
    target_extra = ", ".join(
        part
        for part in (
            device.model,
            device.tran,
            human_bytes(device.size),
            "可移动" if device.removable else "",
            device.note,
        )
        if part
    )
    renderer.field("目标", str(device.path), target_extra)
    renderer.field(
        "模式",
        {"write": "写入 + 回读", "verify": "只回读", "plan": "只预检"}[args.command],
    )

    renderer.checks_header()
    for check in report.checks:
        renderer.check(check)

    def finish(code: int, error: str = "") -> tuple[Report, int]:
        """统一收尾：落报告、打摘要与 AIRY-FLASH 标记。"""
        report.error = error
        report.result = "pass" if code == EXIT_OK else "fail"
        report.finished = now_iso()
        report_path = ""
        if args.report:
            report_path = str(Path(args.report))
            write_report(report, Path(args.report))
        renderer.summary(report, time.monotonic() - started, report_path)
        renderer.marker(report)
        if code == EXIT_OK and args.command == "write":
            renderer.next_steps()
        return report, code

    if any(not c.ok and not c.skipped for c in report.checks):
        return finish(EXIT_CHECK, "预检未通过，未触碰目标设备")

    renderer.stages_header()
    stage_index = 0

    # 1) 校验镜像
    stage_index += 1
    hasher = Stage(stage_index, stages_total, "校验镜像", FAIL)
    renderer.stage_start(hasher)
    clock = time.monotonic()
    meter = _meter(image.size, clock)

    def on_hash(done: int, total: int) -> None:
        rate, eta = meter(done, total)
        renderer.stage_progress(done, total, rate, eta)

    if expected:
        image = ImageFacts(
            path=image.path,
            size=image.size,
            digest=sha256_file(image.path, progress=on_hash),
            expected=image.expected,
            sums_file=image.sums_file,
        )
        report.image = image
        if image.digest_ok:
            hasher = Stage(
                stage_index,
                stages_total,
                "校验镜像",
                OK,
                f"sha256 一致 {short_digest(image.digest)}",
            )
        else:
            hasher = Stage(
                stage_index,
                stages_total,
                "校验镜像",
                FAIL,
                f"摘要不符：实际 {short_digest(image.digest)} ≠ 期望 {short_digest(expected)}",
            )
    else:
        hasher = Stage(stage_index, stages_total, "校验镜像", SKIP, "未提供摘要，跳过")
    renderer.stage_end(hasher)
    report.stages.append(hasher)
    if hasher.status == FAIL:
        return finish(EXIT_CHECK, hasher.detail)

    if args.command == "plan":
        return finish(EXIT_OK)

    # 2) 写入
    if write_mode:
        stage_index += 1
        clock = time.monotonic()
        meter = _meter(image.size, clock)

        def on_write(done: int, total: int) -> None:
            rate, eta = meter(done, total)
            renderer.stage_progress(done, total, rate, eta)

        renderer.stage_start(Stage(stage_index, stages_total, "写入设备", FAIL))
        elapsed_before = time.monotonic()
        written = write_image(
            image.path,
            device.path,
            progress=on_write,
            file_target=device.kind == "file",
        )
        span = max(time.monotonic() - elapsed_before, 1e-6)
        stage = Stage(
            stage_index,
            stages_total,
            "写入设备",
            OK,
            f"{human_bytes(written)}  {human_bytes(written / span)}/s",
        )
        renderer.stage_end(stage)
        report.stages.append(stage)

        # 3) 刷盘
        stage_index += 1
        clock = time.monotonic()
        renderer.stage_start(Stage(stage_index, stages_total, "刷盘同步", FAIL))
        flush_device(device.path)
        stage = Stage(
            stage_index,
            stages_total,
            "刷盘同步",
            OK,
            f"fsync 完成 {human_duration(time.monotonic() - clock)}",
        )
        renderer.stage_end(stage)
        report.stages.append(stage)

    # 4) 回读
    stage_index += 1
    clock = time.monotonic()
    renderer.stage_start(Stage(stage_index, stages_total, "回读校验", FAIL))
    if args.verify == "none":
        stage = Stage(stage_index, stages_total, "回读校验", SKIP, "--verify none")
    else:
        same, detail, _ranges = readback(image.path, device.path, args.verify)
        stage = Stage(
            stage_index,
            stages_total,
            "回读校验",
            OK if same else FAIL,
            f"{detail}  {human_duration(time.monotonic() - clock)}",
        )
    renderer.stage_end(stage)
    report.stages.append(stage)
    if stage.status == FAIL:
        return finish(EXIT_IO, stage.detail)

    return finish(EXIT_OK)


def write_report(report: Report, path: Path) -> None:
    payload = report.as_dict(TOOL_VERSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return
    rows: list[str] = [f"airymax-flash-report v1 (airy-flash {TOOL_VERSION})"]
    for key, value in (
        ("result", report.result),
        ("command", report.command),
        ("host", report.host),
        ("started", report.started),
        ("finished", report.finished),
        ("verify", report.verify),
        ("argv", " ".join(report.argv)),
    ):
        rows.append(f"{key}={value}")
    if report.image:
        rows.append(f"image.path={report.image.path}")
        rows.append(f"image.bytes={report.image.size}")
        rows.append(f"image.sha256={report.image.digest}")
        rows.append(f"image.sha256.expected={report.image.expected}")
        rows.append(f"image.sha256.source={report.image.sums_file}")
    if report.device:
        rows.append(f"device.path={report.device.path}")
        rows.append(f"device.kind={report.device.kind}")
        rows.append(f"device.bytes={report.device.size}")
        rows.append(f"device.model={report.device.model}")
        rows.append(f"device.serial={report.device.serial}")
    for check in report.checks:
        rows.append(
            f"check.{check.name}={SKIP if check.skipped else ('pass' if check.ok else 'fail')}"
        )
    for stage in report.stages:
        rows.append(f"stage.{stage.index}.{stage.name}={stage.status} ({stage.detail})")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


# ─── CLI ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airy-flash",
        description="把 AirymaxOS ISO 可复现地写入 U 盘或整块设备",
        epilog="退出码: 0 成功 / 1 预检或校验不通过 / 2 用法或环境错误 / 3 写入或回读失败",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="plan",
        choices=("plan", "write", "verify", "check"),
        help="plan 只预检（默认）；write 写入；verify 只回读；check 环境自检",
    )
    parser.add_argument("--image", help="ISO 或 raw 镜像路径")
    parser.add_argument("--device", help="目标块设备，如 /dev/sdb")
    parser.add_argument("--sha256", default="", help="直接给出期望的 sha256")
    parser.add_argument("--sums", default="", help="sha256sum 文件路径")
    parser.add_argument(
        "--verify", default="sample", choices=VERIFY_MODES, help="回读强度（默认 sample）"
    )
    parser.add_argument("--yes", action="store_true", help="确认覆盖目标设备")
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="没有发布摘要时仍然继续（不推荐）",
    )
    parser.add_argument(
        "--allow-file", action="store_true", help="允许目标是普通文件（测试/虚机镜像）"
    )
    parser.add_argument("--report", help="把本次结果写成 .json 或 key=value 记录")
    parser.add_argument("--json", action="store_true", help="stdout 输出机器可读 JSON")
    parser.add_argument("--no-progress", action="store_true", help="不输出进度条")
    parser.add_argument("--mounts", help="调试：用指定 mounts 文件替代 /proc/mounts")
    parser.add_argument(
        "--sys-block", default="/sys/class/block", help="调试：sysfs 块设备根"
    )
    parser.add_argument("--version", action="version", version=f"airy-flash {TOOL_VERSION}")
    return parser


def _self_check(args: argparse.Namespace) -> int:
    rows = [
        ("python", sys.version.split()[0], True),
        (
            "/proc/mounts",
            "可读" if Path("/proc/mounts").exists() else "缺失",
            Path("/proc/mounts").exists(),
        ),
        (
            "/sys/class/block",
            "存在" if Path("/sys/class/block").is_dir() else "缺失",
            Path("/sys/class/block").is_dir(),
        ),
        ("root", "是" if os.geteuid() == 0 else "否（写块设备时需要 sudo）", os.geteuid() == 0),
    ]
    if args.json:
        sys.stdout.write(
            json.dumps(
                {
                    "tool": "airy-flash",
                    "version": TOOL_VERSION,
                    "command": "check",
                    "result": "pass",
                    "checks": [
                        {"name": name, "detail": detail, "ok": ok}
                        for name, detail, ok in rows
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return EXIT_OK
    renderer = Renderer(tty=False if args.no_progress else None)
    renderer.title(f"AirymaxOS 烧录工具 {TOOL_VERSION}  (check)")
    renderer.checks_header()
    for name, detail, ok in rows:
        renderer.check(Check(name, ok, detail, skipped=not ok and name == "root"))
    renderer.section("结果")
    renderer.note("用法    airy-flash write --image X.iso --device /dev/sdX --yes")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.argv = list(argv) if argv is not None else None

    try:
        if args.command == "check":
            return _self_check(args)
        if not args.image or not args.device:
            parser.error("需要 --image 与 --device（check 子命令除外）")
        report, code = execute(args)
        if args.json:
            sys.stdout.write(
                json.dumps(report.as_dict(TOOL_VERSION), ensure_ascii=False, indent=2)
                + "\n"
            )
        return code
    except FlashError as exc:
        if getattr(args, "json", False):
            sys.stdout.write(
                json.dumps(
                    {
                        "tool": "airy-flash",
                        "version": TOOL_VERSION,
                        "command": args.command,
                        "result": "fail",
                        "error": exc.message,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        else:
            sys.stderr.write(f"airy-flash: {exc.message}\n")
        return exc.code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
