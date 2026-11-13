#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
airy_flash.py — AirymaxOS 烧录工具

把「下载好的 ISO → U 盘/整块设备」这件事收敛成一条可复现、可取证的命令：

  airy-flash fetch  --oci X.iso-oci.txt --out DIR        # 取回分块并拼成 ISO
  airy-flash fetch  --ref REG/REPO:TAG --out DIR         # 同上，清单即账本
  airy-flash plan   --image X.iso --device /dev/sdX     # 只预检，不碰设备
  airy-flash write  --image X.iso --device /dev/sdX --yes
  airy-flash verify --image X.iso --device /dev/sdX      # 校镜像 + 回读比对
  airy-flash check                                       # 环境自检

设计要点：
  * 预检与写入分离。plan 不打开目标设备写句柄，可先对着实机跑到全绿。
  * fetch 把发布清单当唯一账本：iso-oci.txt 里的逐块 sha256 既是下载参数，
    也是校验依据；分块逐个核对后按序拼接，再核对整体 sha256，最后与
    write 共用同一条 .sha256 解析链，因此「取回」与「烧录」之间不留空档。
  * 目标设备用 sysfs 判定：块设备类型、容量、只读、挂载点、是否系统盘。
    「系统盘」按 /proc/mounts 里 /、/boot、/boot/efi、/usr、/var 的来源
    反查父磁盘，而不是靠名字猜 sdX。
  * 校验和先于写入：镜像摘要必须在写入前核对发布 .sha256，缺失则拒绝，
    除非显式 --allow-unverified。
  * 写入后回读比对：sample 抽头部/中部/尾部三窗，full 全量，none 跳过。
  * 输出分「映像/目标/模式/预检/阶段/结果/下一步」七段，预检项与阶段共用
    一套列宽；TTY 下带进度条与吞吐，非 TTY 退化为逐行里程碑；长阻塞的
    fsync 与回读按固定间隔吐心跳，任何一个阶段都不会静默卡住。
  * 失败与成功走同一条收尾路径：都落 --report、都打建议行、都以
    AIRY-FLASH-PASS/FAIL 标记行结束，便于串口日志与 CI 采集。

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
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
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
    OciLedger,
    OciPart,
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
    "ledger_from_ref",
    "ledger_of",
    "parse_oci",
    "parse_sums",
    "probe_device",
    "probe_image",
    "read_mounts",
    "readback",
    "resolve_exp",
    "sample_ranges",
    "sha256_file",
    "system_disks",
    "write_image",
]

# 发行版树内版本写在 distro/release/os-release 的 VERSION_ID；本工具被打成
# 单文件 pyz 后脱离仓库运行，读不到那份声明，只能各自持有字面量。两者必须
# 一致，由 ci/gates/tool-pack.sh 在打包后实跑 --version 比对，不一致即判负。
TOOL_VERSION = "0.0.4"

CHUNK = 4 << 20          # 单次读写块，4 MiB：对 U 盘与页缓存都友好
SAMPLE_WINDOW = 16 << 20  # 抽检窗口
CRITICAL_MOUNTS = ("/", "/boot", "/boot/efi", "/usr", "/var")
BLOCK_SECTOR = 512
SPACE_SLACK = 64 << 20   # 取回时除镜像本体外还给目录留出的余量
OCI_KEYS = ("ref", "tag", "name", "sha256", "size", "chunk_size", "chunks")
# 清单注解前缀与 layer 标题键：ci/gates/iso-publish.sh 以同一组键推送，
# 取件侧只按这组键读，避免两侧各自发明字段名。
ANN = "org.openairymax.iso"
TITLE = "org.opencontainers.image.title"


# ─── 人读格式化 ──────────────────────────────────────────────────────────────


BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def _unit_text(size: float, unit: str) -> str:
    """整数字节不带小数，其余单位保留两位。"""
    if unit == "B":
        return f"{size:.0f} {unit}"
    return f"{size:.2f} {unit}"


def human_bytes(value: float) -> str:
    """字节数 → "3.43 GiB" 这类固定三位有效数字的短表示。"""
    size = float(value)
    for unit in BYTE_UNITS[:-1]:
        if size < 1024:
            return _unit_text(size, unit)
        size /= 1024
    return _unit_text(size, BYTE_UNITS[-1])


def human_duration(seconds: float) -> str:
    """秒 → "0.4s" / "42s" / "1m06s" / "1h02m"。

    十秒以内保留一位小数：整秒取整会把亚秒阶段压成 "0s"，
    令 fsync、单块校验这类快阶段在报告里看起来像没有执行。
    """
    if seconds < 9.95:
        return f"{seconds:.1f}s"
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


def _bare_name(name: str) -> str:
    """去掉 sha256sum 的二进制标记 `*` 与 `./` 前缀。"""
    name = name.strip().lstrip("*").strip()
    while name.startswith("./"):
        name = name[2:]
    return name


def _sum_pair(line: str) -> tuple[str, str] | None:
    """单行 → (摘要, 文件名)；畸形行返回 None。"""
    parts = line.split(None, 1)
    if len(parts) != 2:
        return None
    digest = parts[0].lower()
    if not _is_hex64(digest):
        return None
    return digest, _bare_name(parts[1])


def _sum_line(raw: str):
    """有效行 → (digest, name)；空行、注释与非法行返回 None。"""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    return _sum_pair(line)


def parse_sums(text: str) -> dict[str, str]:
    """解析 sha256sum 格式（兼容二进制标记 `*` 与 `./` 前缀）。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        pair = _sum_line(raw)
        if pair is not None:
            out[Path(pair[1]).name] = pair[0]
    return out


def _explicit_sum(raw: str) -> str:
    """--sha256 必须自带 64 位小写十六进制；大小写不敏感。"""
    value = raw.strip().lower()
    if not _is_hex64(value):
        raise FlashError(EXIT_USAGE, f"--sha256 不是 64 位十六进制: {raw}")
    return value


def _sum_files(image: Path, sums: str) -> list[Path]:
    """校验和候选：显式 --sums 优先，其次是镜像旁的 .sha256 与 SHA256SUMS。"""
    extra = [Path(sums)] if sums else []
    return extra + [image.with_suffix(image.suffix + ".sha256"),
                    image.parent / "SHA256SUMS"]


def _sum_text(candidate: Path) -> str | None:
    """读取校验和文件正文；不可读时返回 None。"""
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _sum_only(table: dict[str, str], image: Path,
              candidate: Path) -> tuple[str, str]:
    """显式 --sums 下唯一一条摘要可代为匹配；否则当场报错。"""
    if len(table) == 1:
        return next(iter(table.values())), str(candidate)
    raise FlashError(EXIT_CHECK,
                     f"{candidate} 中没有 {image.name} 的摘要")


def _sum_pick(candidate: Path, image: Path,
              explicit: bool) -> tuple[str, str] | None:
    """从单个校验和文件取摘要；显式给出却查无此项当场报错。"""
    text = _sum_text(candidate)
    if text is None:
        return None
    table = parse_sums(text)
    if image.name in table:
        return table[image.name], str(candidate)
    if not explicit:
        return None
    return _sum_only(table, image, candidate)


def resolve_exp(
    image: Path, *, sha256: str = "", sums: str = ""
) -> tuple[str, str]:
    """返回 (期望摘要, 来源说明)；来源为空表示没有可用校验和。"""
    if sha256:
        return _explicit_sum(sha256), "--sha256"
    for candidate in _sum_files(image, sums):
        hit = _sum_pick(candidate, image, bool(sums))
        if hit is not None:
            return hit
    return "", ""


# ─── 发布清单 ───────────────────────────────────────────────────────────────


def _oci_int(value: str, label: str, path: Path) -> int:
    try:
        number = int(value, 10)
    except ValueError:
        raise FlashError(
            EXIT_CHECK, f"{path.name} 的 {label} 不是整数: {value}"
        ) from None
    if number <= 0:
        raise FlashError(EXIT_CHECK, f"{path.name} 的 {label} 必须为正: {value}")
    return number


def _oci_digest(raw: str, label: str, path: Path) -> str:
    """摘要只有一种合法形状：64 位小写十六进制；两处清单共用这一判据。"""
    digest = raw.strip().lower()
    if not _is_hex64(digest):
        raise FlashError(EXIT_CHECK, f"{path.name} {label}")
    return digest


def _oci_seq(fields: list[str], index: int, path: Path) -> None:
    """首列必须是随位置递增的块序号：序号错位就是块错位。"""
    if not fields or not fields[0].isdigit() or int(fields[0]) != index:
        raise FlashError(
            EXIT_CHECK, f"{path.name} 的 part 序号应从 0 连续递增（第 {index + 1} 行不符）"
        )


def _oci_kv(tokens: list[str]) -> dict[str, str]:
    """`key=value` token → 字段表；无等号的噪声 token 跳过。"""
    out: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep:
            out[key] = value
    return out


def _oci_part(index: int, rest: str, path: Path) -> OciPart:
    """解析一行 `part=0000 sha256=… size=… name=…`，序号须与出现位置一致。

    拼接按清单顺序进行，序号错位就是块错位，因此这里不看序号只看位置是
    不行的：必须逐行相等，否则 3.4 GiB 会拼出一份静默损坏的镜像。
    """
    fields = rest.split()
    _oci_seq(fields, index, path)
    tokens = _oci_kv(fields[1:])
    missing = [key for key in ("sha256", "size", "name") if not tokens.get(key)]
    if missing:
        raise FlashError(
            EXIT_CHECK, f"{path.name} 第 {index} 块缺少 {', '.join(missing)}"
        )
    digest = _oci_digest(tokens["sha256"], f"第 {index} 块 sha256 格式不对", path)
    size = _oci_int(tokens["size"], f"part[{index}].size", path)
    return OciPart(index, digest, size, tokens["name"])


def _oci_fields(text: str) -> dict[str, str]:
    """非 `part=` 的已知键；空行、注释与噪声行都不产生字段。"""
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        key, sep, rest = raw.strip().partition("=")
        if sep and key.strip() in OCI_KEYS:
            fields[key.strip()] = rest.strip()
    return fields


def _oci_part_lines(text: str, path: Path) -> list[OciPart]:
    """`part=` 行按出现顺序编号；其余行不参与分块账本。"""
    parts: list[OciPart] = []
    for raw in text.splitlines():
        key, sep, rest = raw.strip().partition("=")
        if sep and key.strip() == "part":
            parts.append(_oci_part(len(parts), rest, path))
    return parts


def _oci_lines(text: str, path: Path) -> tuple[dict[str, str], list[OciPart]]:
    """逐行归类：`part=` 按出现顺序编号，其余已知键进字段表，注释与噪声跳过。"""
    return _oci_fields(text), _oci_part_lines(text, path)


def _oci_need(fields: dict[str, str], path: Path) -> None:
    """五个必填字段缺一即拒：缺字段的清单无法判断制品是否完整。"""
    missing = [
        key for key in ("ref", "name", "sha256", "size", "chunk_size")
        if not fields.get(key)
    ]
    if missing:
        raise FlashError(EXIT_CHECK, f"{path.name} 缺少字段 {', '.join(missing)}")


def _oci_declared(fields: dict[str, str], parts: list[OciPart],
                  path: Path) -> int:
    """声明块数；缺省时取实际块数。"""
    raw = fields.get("chunks") or str(len(parts))
    return _oci_int(raw, "chunks", path)


def _span_bytes(path: Path, parts: list[OciPart], size: int) -> None:
    """分块字节合计必须等于声明大小；不等即报错。"""
    joined = sum(part.size for part in parts)
    if joined != size:
        raise FlashError(
            EXIT_CHECK,
            f"{path.name} 分块合计 {human_bytes(joined)} ≠ 声明 {human_bytes(size)}",
        )


def _oci_span(path: Path, fields: dict[str, str],
              parts: list[OciPart]) -> int:
    """块数与字节合计必须同时对上声明：对不上就是制品与清单不一致。"""
    size = _oci_int(fields["size"], "size", path)
    declared = _oci_declared(fields, parts, path)
    if declared != len(parts):
        raise FlashError(
            EXIT_CHECK, f"{path.name} 声明 {declared} 块却列出 {len(parts)} 块"
        )
    _span_bytes(path, parts, size)
    return size


def parse_oci(path: Path) -> OciLedger:
    """把 iso-oci.txt 读成账本；分块不自洽当场拦下，不做半懂不懂的猜测。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FlashError(
            EXIT_USAGE,
            f"读不到发布清单 {path}: {exc.strerror or exc}",
            hint="从发布页取回与镜像同名的 .iso-oci.txt",
        ) from exc
    fields, parts = _oci_lines(text, path)
    _oci_need(fields, path)
    if not parts:
        raise FlashError(EXIT_CHECK, f"{path.name} 没有 part= 行")
    digest = _oci_digest(fields["sha256"], "的 sha256 格式不对", path)
    size = _oci_span(path, fields, parts)
    return OciLedger(
        ref=fields["ref"],
        tag=fields.get("tag", ""),
        name=fields["name"],
        sha256=digest,
        size=size,
        chunk_size=_oci_int(fields["chunk_size"], "chunk_size", path),
        parts=tuple(parts),
    )


def _ann(ann: dict[str, str], key: str, ref: str) -> str:
    """注解缺一即拒：早于分块实现的 tag 只有 created，按它取件必然错位。"""
    value = str(ann.get(f"{ANN}.{key}") or "").strip()
    if not value:
        raise FlashError(
            EXIT_CHECK,
            f"{ref} 缺少注解 {ANN}.{key}",
            hint="该 tag 早于分块实现；按发布页给出的 tag 重新取件",
        )
    return value


def _positive(raw: str, label: str, ref: str) -> int:
    """正整数解析：块大小与块数错一个数量级都会拼出损坏镜像。"""
    try:
        number = int(raw, 10)
    except ValueError:
        raise FlashError(EXIT_CHECK, f"{ref} 的 {label} 不是整数: {raw}") from None
    if number <= 0:
        raise FlashError(EXIT_CHECK, f"{ref} 的 {label} 必须为正: {raw}")
    return number


def _ann_int(ann: dict[str, str], key: str, ref: str) -> int:
    return _positive(_ann(ann, key, ref), f"{ANN}.{key}", ref)


def _is_hex64(value: str) -> bool:
    """裸 sha256：64 位小写十六进制；digest 与注解摘要共用这一判据。"""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _digest(value: str, ref: str) -> str:
    """layer digest → 裸 sha256；算法或长度不对当场拒，不猜「大概是它」。"""
    algo, _, hexd = value.partition(":")
    hexd = hexd.lower()
    if algo != "sha256" or not _is_hex64(hexd):
        raise FlashError(EXIT_CHECK, f"{ref} 的 layer digest 不是 sha256: {value}")
    return hexd


def _split_ref(ref: str) -> tuple[str, str]:
    """ghcr.io/o/i:v0.0.3 → (ghcr.io/o/i, v0.0.3)；tag 缺省时为空串。"""
    head, sep, tail = ref.rpartition(":")
    if not sep or "/" in tail:
        return ref, ""
    return head, tail


def _manifest_doc(ref: str, text: str) -> dict:
    """清单文本 → 清单对象；oras 1.2 起的外层信封在这里剥掉。

    oras 1.2 起 `manifest fetch --format json` 输出
    {reference, digest, size, content:{…}}，注解与 layers 都下沉到 content；
    更早的 oras 直接吐裸 manifest。两种形态都认，否则取件在版本差异上会
    误判成「tag 早于分块实现」。
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FlashError(EXIT_CHECK, f"{ref} 的清单不是合法 JSON: {exc}") from None
    if isinstance(doc.get("content"), dict):
        doc = doc["content"]
    return doc


def _ledger_ann(ref: str, ann: dict[str, str]):
    """注解即元数据：五项一次取齐，缺一项立刻拒而不是按缺省值往下走。"""
    name = _ann(ann, "name", ref)
    sha = _ann(ann, "sha256", ref).lower()
    if not _is_hex64(sha):
        raise FlashError(EXIT_CHECK, f"{ref} 的 {ANN}.sha256 格式不对")
    return (name, sha, _ann_int(ann, "size", ref),
            _ann_int(ann, "chunk-size", ref), _ann_int(ann, "chunks", ref))


def _layer_title(layer: dict) -> str:
    """层标题注解；缺失时为空串（标题不参与完整性判定）。"""
    return str((layer.get("annotations") or {}).get(TITLE) or "").strip()


def _layer_size(index: int, layer: dict, ref: str) -> int:
    """层大小必须已声明为数字串，缺失即拒绝猜测。"""
    raw_size = str(layer.get("size") or "")
    if not raw_size:
        raise FlashError(EXIT_CHECK, f"{ref} 的第 {index} 层缺少 size")
    return _positive(raw_size, f"layer[{index}].size", ref)


def _ledger_parts(ref: str, layers: list[dict]) -> list[OciPart]:
    """逐层账本：标题、digest 与 size 三项缺一不可，缺谁都不猜。"""
    parts: list[OciPart] = []
    for index, layer in enumerate(layers):
        title = _layer_title(layer)
        size = _layer_size(index, layer, ref)
        digest = _digest(str(layer.get("digest") or ""), ref)
        parts.append(OciPart(index, digest, size, title))
    return parts


def _ledger_order(ref: str, parts: list[OciPart]) -> None:
    """层序就是块序：清单被重排过时按序拼接会得到错位镜像。"""
    names = [part.name for part in parts]
    if names != sorted(names):
        raise FlashError(
            EXIT_CHECK,
            f"{ref} 的 layer 顺序与块序不一致",
            hint="清单被重排过；改用发布页给出的 tag",
        )


def _ledger_bytes(ref: str, parts: list[OciPart], size: int) -> None:
    """分块字节合计必须等于声明大小。"""
    joined = sum(part.size for part in parts)
    if joined != size:
        raise FlashError(
            EXIT_CHECK,
            f"{ref} 分块合计 {human_bytes(joined)} ≠ 声明 {human_bytes(size)}",
        )


def _ledger_chunks(ref: str, parts: list[OciPart], chunk: int) -> None:
    """除末块外，每块大小必须等于声明块大小。"""
    if any(part.size != chunk for part in parts[:-1]):
        raise FlashError(EXIT_CHECK, f"{ref} 的块大小与 {ANN}.chunk-size 不一致")


def _ledger_sums(ref: str, parts: list[OciPart], size: int, chunk: int) -> None:
    """分块账本的算术自洽：合计等于声明大小，除末块外每块等于块大小。"""
    _ledger_bytes(ref, parts, size)
    _ledger_chunks(ref, parts, chunk)


def ledger_of(ref: str, text: str) -> OciLedger:
    """把 OCI 清单读成账本：tag 自身就是账本，不再依赖外部文件分发。

    公开面只有 OCI 清单（注解 + layer）。账本文件此前只作为需登录的
    Actions 制品上传，外部用户无从取件。此处以注解为元数据来源、以 layer
    的标题与 digest 为逐块账本，使 --ref 与 --oci 得到同一份结论。
    """
    doc = _manifest_doc(ref, text)
    ann = doc.get("annotations") or {}
    name, sha, size, chunk, chunks = _ledger_ann(ref, ann)
    layers = doc.get("layers") or []
    if len(layers) != chunks:
        raise FlashError(
            EXIT_CHECK, f"{ref} 声明 {chunks} 块却列出 {len(layers)} 层")
    parts = _ledger_parts(ref, layers)
    _ledger_order(ref, parts)
    _ledger_sums(ref, parts, size, chunk)
    repo, tag = _split_ref(ref)
    return OciLedger(
        ref=repo,
        tag=tag,
        name=name,
        sha256=sha,
        size=size,
        chunk_size=chunk,
        parts=tuple(parts),
    )


def ledger_from_ref(ref: str, oras: str) -> OciLedger:
    """取远端清单再解析；网络或凭据问题归 IO，清单不自洽归校验。"""
    proc = subprocess.run(
        [oras, "manifest", "fetch", "--format", "json", ref],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        detail = tail[-1] if tail else f"退出 {proc.returncode}"
        raise FlashError(
            EXIT_IO,
            f"oras manifest fetch 失败: {detail}",
            hint="仓库或网络不可达；换镜像站后重试同一条命令",
        )
    return ledger_of(ref, proc.stdout)


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


def _partitions(entry: Path, name: str) -> tuple[tuple[str, ...], int]:
    """整盘上现有的分区名与字节合计，只读 sysfs。

    写入会连同分区表一起覆盖，操作者却只能从盘符反推「这块盘原来装的是
    什么」。sysfs 里属分区的子项带 `partition` 属性，据此区分 queue/等
    目录项，不需要调 blkid（它要 root，且对未挂载的盘也不总给得出结果）。
    """
    if not entry.is_dir():
        return (), 0
    names: list[str] = []
    total = 0
    for child in sorted(entry.iterdir()):
        if _skip_child(child, name):
            continue
        names.append(child.name)
        total += _part_bytes(child)
    return tuple(names), total


def _skip_child(child: Path, name: str) -> bool:
    """盘符自身或没有 partition 属性的目录项不是分区。"""
    return child.name == name or not (child / "partition").exists()


def _part_bytes(child: Path) -> int:
    """分区 size 属性（512B 扇区）折算字节；不可解析记 0。"""
    sectors = _read_attr(child / "size")
    return int(sectors) * BLOCK_SECTOR if sectors.isdigit() else 0


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
        raise FlashError(
            EXIT_USAGE,
            f"镜像不可读: {path} ({exc.strerror})",
            hint="核对 --image 路径；发布物请先用 oras pull 取回",
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise FlashError(
            EXIT_USAGE,
            f"镜像不是普通文件: {target}",
            hint="--image 指向 ISO 文件本身，不是目录或设备节点",
        )
    if info.st_size == 0:
        raise FlashError(
            EXIT_CHECK, f"镜像是空文件: {target}", hint="重新取件；空文件通常是中断的下载"
        )
    return ImageFacts(path=target, size=info.st_size)


def _stat_target(target: Path) -> os.stat_result:
    """目标必须可 stat；不可达时给出权限提示而不是裸 OSError。"""
    try:
        return target.stat()
    except OSError as exc:  # pragma: no cover - exists() 已过
        raise FlashError(
            EXIT_CHECK,
            f"无法 stat 目标: {target}",
            hint="确认该节点在当前用户可见（写块设备需 sudo）",
        ) from exc


def _mount_points(table: Sequence[Mount], prefix: str) -> tuple[str, ...]:
    """目标自身及其分区的挂载点，去重后按字典序。"""
    hits = {m.point for m in table
            if m.source == prefix or m.source.startswith(prefix)}
    return tuple(sorted(hits))


def _block_facts(target: Path, table: Sequence[Mount],
                 sys_block: Path) -> DeviceFacts:
    """块设备：容量、型号、序列号、总线与只读位全部取自 sysfs。"""
    entry = Path(sys_block) / target.name
    dev = entry / "device"
    sectors = _read_attr(entry / "size")
    size = int(sectors) * BLOCK_SECTOR if sectors.isdigit() else 0
    parts, parts_bytes = _partitions(entry, target.name)
    return DeviceFacts(
        path=target,
        kind="block",
        size=size,
        model=_read_attr(dev / "model"),
        serial=_read_attr(dev / "serial"),
        tran=_read_attr(dev / "transport") or _read_attr(dev / "subsystem"),
        removable=_read_attr(entry / "removable") == "1",
        read_only=_read_attr(entry / "ro") == "1",
        mountpoints=_mount_points(table, target.as_posix()),
        system_disks=system_disks(table, sys_block),
        parts=parts,
        parts_bytes=parts_bytes,
    )


def _file_facts(target: Path, info: os.stat_result,
                allow_file: bool) -> DeviceFacts:
    """普通文件只在显式 --allow-file 时接受，用于虚拟机镜像与测试。"""
    if not allow_file:
        raise FlashError(
            EXIT_CHECK,
            f"{target} 是普通文件；写镜像到文件需显式 --allow-file",
            hint="目标应为整块设备；仅虚拟机镜像或测试才用 --allow-file",
        )
    return DeviceFacts(
        path=target,
        kind="file",
        size=info.st_size,
        note="普通文件靶子（测试/虚拟机镜像）",
    )


def _block_or_file(target: Path, info: os.stat_result,
                   table: Sequence[Mount], sys_block: Path,
                   allow_file: bool) -> DeviceFacts:
    """按文件类型取证；块设备与普通文件之外一律拒绝。"""
    if stat.S_ISBLK(info.st_mode):
        return _block_facts(target, table, sys_block)
    if stat.S_ISREG(info.st_mode):
        return _file_facts(target, info, allow_file)
    raise FlashError(
        EXIT_CHECK,
        f"目标既不是块设备也不是普通文件: {target}",
        hint="传整盘节点（/dev/sdb），不是分区（/dev/sdb1）",
    )


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
        raise FlashError(
            EXIT_CHECK, f"目标不存在: {target}", hint="用 lsblk 取整盘节点，如 /dev/sdb"
        )

    info = _stat_target(target)
    table = list(mounts) if mounts is not None else read_mounts()
    return _block_or_file(target, info, table, sys_block, allow_file)


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
    return _target_checks(device) + [
        _capacity_check(device, image),
        _digest_check(expected, source, allow_unverified),
    ]


def _block_check(device: DeviceFacts) -> Check:
    if device.kind == "block":
        return Check("目标是块设备", True, str(device.path))
    return Check(
        "目标是块设备", True, f"{device.path}（普通文件，测试模式）", skipped=True
    )


def _system_check(device: DeviceFacts) -> Check:
    """普通文件靶子上判「系统盘: （未识别）」既非通过也非失败，整条标 skipped。"""
    hosts = ", ".join(device.system_disks) or "（未识别）"
    if device.kind != "block":
        return Check("非系统盘", True, "普通文件靶子无系统盘概念", skipped=True)
    return Check(
        "非系统盘",
        not device.is_system_disk,
        f"承载 {', '.join(CRITICAL_MOUNTS)} 的磁盘: {hosts}"
        if device.is_system_disk
        else f"系统盘: {hosts}",
        fix="换一块与系统盘无关的盘；写入会覆盖整盘",
    )


def _mount_check(device: DeviceFacts) -> Check:
    return Check(
        "未挂载",
        not device.mountpoints,
        f"已挂载于 {', '.join(device.mountpoints)}，请先 umount"
        if device.mountpoints
        else "无挂载点",
        fix="umount 该盘上的所有挂载点后重试",
    )


def _ro_check(device: DeviceFacts) -> Check:
    if device.kind != "block":
        return Check("非只读", True, "普通文件读写", skipped=True)
    return Check(
        "非只读",
        not device.read_only,
        "设备处于只读状态（写保护开关或 ro 属性）"
        if device.read_only
        else "可写",
        fix="关闭写保护开关；卡式读卡器或 USB 写保护会置 ro",
    )


def _target_checks(device: DeviceFacts) -> list[Check]:
    """靶子四项：块设备、非系统盘、未挂载、非只读。

    「非系统盘」「非只读」只对块设备有定义：普通文件靶子上判「系统盘:
    （未识别）」既非通过也非失败，读起来像结论，实际什么也没说，整条标
    skipped 才是准确的表述。顺序与报告里的阅读顺序一致，不要重排。
    """
    return [
        _block_check(device),
        _system_check(device),
        _mount_check(device),
        _ro_check(device),
    ]


def _capacity_check(device: DeviceFacts, image: ImageFacts) -> Check:
    enough = device.size >= image.size
    return Check(
        "容量充足",
        enough,
        f"{human_bytes(device.size)} {'≥' if enough else '<'} "
        f"{human_bytes(image.size)}",
        fix=f"换用容量不小于 {human_bytes(image.size)} 的设备",
    )


def _digest_check(expected: str, source: str, allow_unverified: bool) -> Check:
    if expected:
        return Check("校验和来源", True, source)
    if allow_unverified:
        return Check("校验和来源", True, "未提供发布摘要（--allow-unverified）", skipped=True)
    return Check(
        "校验和来源",
        False,
        "缺少发布 .sha256；用 --sha256/--sums 指定，或显式 --allow-unverified",
        fix="从发布页取回同名 .sha256，或用 --sha256 直接给出摘要",
    )


# ─── 写入与回读 ─────────────────────────────────────────────────────────────


def _write_all(dst: io.FileIO, block: bytes) -> None:
    """短写循环：单次 write 可能只落一部分。"""
    offset = 0
    while offset < len(block):
        offset += dst.write(block[offset:])


# 写入途中的失败几乎都不是「磁盘满了」：容量预检已经按镜像大小放行，块设备
# 顺序写却在中途报 ENOSPC，实测是 U 盘掉线后内核把 offline 的失败写映射成
# 空间不足（dmesg 里同时出现 device offline error）。把 errno 翻成处置动作，
# 免得使用者照着「空间不足」去换更大的盘。
_IO_HINTS: tuple[tuple[int, str], ...] = (
    (errno.ENOSPC, "介质在写入途中断开或掉线；重插 U 盘后重试，仍失败则换口或换线"),
    (errno.EIO, "介质读写中断或出现坏块；重插后重试，仍失败则换盘"),
    (errno.ENODEV, "介质已离线（设备节点消失）；重插 U 盘后再跑"),
    (errno.ENXIO, "介质已离线（设备节点不可用）；重插 U 盘后再跑"),
    (errno.EACCES, "写块设备需要 root；用 sudo 重跑"),
    (errno.EPERM, "写块设备需要 root；用 sudo 重跑"),
)


def _io_hint(exc: OSError) -> str:
    """把写/刷盘的 errno 翻成一句话处置；未知 errno 给通用建议。"""
    for code, hint in _IO_HINTS:
        if exc.errno == code:
            return hint
    return "确认介质接触与供电；仍失败则换一块盘"


def _copy_stream(
    src: io.BufferedReader,
    dst: io.FileIO,
    chunk: int,
    progress: Callable[[int, int], None] | None,
    total: int,
) -> int:
    """顺序拷贝整个镜像，返回写入字节数；失败时按已写字节数报偏移。"""
    written = 0
    try:
        while True:
            block = src.read(chunk)
            if not block:
                return written
            _write_all(dst, block)
            written += len(block)
            _tick(progress, written, total)
    except OSError as exc:
        raise _write_fail(written, exc) from exc


def _tick(progress: Callable[[int, int], None] | None,
          written: int, total: int) -> None:
    """有进度回调时才上报已写字节。"""
    if progress is not None:
        progress(written, total)


def _write_fail(written: int, exc: OSError) -> FlashError:
    """按已写字节数构造带偏移的写入错误。"""
    return FlashError(
        EXIT_IO,
        f"写入失败于偏移 {human_bytes(written)}: {exc.strerror or exc}",
        hint=_io_hint(exc),
    )


def _truncate(dst, total: int, written: int) -> None:
    """截断到镜像大小；失败转成 FlashError。"""
    try:
        os.ftruncate(dst.fileno(), total)
    except OSError as exc:
        raise FlashError(
            EXIT_IO,
            f"截断失败于偏移 {human_bytes(written)}: {exc.strerror or exc}",
            hint=_io_hint(exc),
        ) from exc


def _trim_file(dst, total: int, written: int, file_target: bool) -> None:
    """普通文件靶子按镜像大小截断；设备靶子跳过。"""
    if file_target and total:
        _truncate(dst, total, written)


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
    with open(image, "rb") as src, open(device, "r+b", buffering=0) as dst:
        written = _copy_stream(src, dst, chunk, progress, total)
        _trim_file(dst, total, written, file_target)
    return written


def flush_device(device: Path) -> None:
    """独立成阶段：U 盘的 fsync 常常比写入本身还久。"""
    fd = _open_for_flush(device)
    try:
        _fsync(fd)
    finally:
        os.close(fd)


def _open_for_flush(device: Path) -> int:
    """只读打开设备以便 fsync；权限不足与其它 IO 错误分开报。"""
    try:
        return os.open(device, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise FlashError(EXIT_IO, "刷盘需要写权限，请以 root 运行") from exc
        raise FlashError(EXIT_IO, f"刷盘失败: {exc.strerror or exc}") from exc


def _fsync(fd: int) -> None:
    """fsync 失败时带上 errno 对应的处置建议。"""
    try:
        os.fsync(fd)
    except OSError as exc:
        raise FlashError(
            EXIT_IO, f"fsync 失败: {exc.strerror or exc}", hint=_io_hint(exc)
        ) from exc


def sample_ranges(size: int, window: int = SAMPLE_WINDOW) -> list[tuple[int, int]]:
    """抽检区间：头/中/尾三窗；镜像不足三窗时退化为全量。"""
    if size <= window * 3:
        return [(0, size)]
    middle = size // 2 - window // 2
    return [(0, window), (middle, middle + window), (size - window, size)]


def _rb_diff(left: bytes, right: bytes, cursor: int) -> str:
    """同长两块比对；不一致时给出首个差异偏移，一致时返回空串。"""
    if len(left) != len(right):
        return f"偏移 {cursor} 读取长度不一致"
    if left == right:
        return ""
    index = next((i for i, (a, b) in enumerate(zip(left, right)) if a != b), 0)
    return f"偏移 {cursor + index} 内容不一致"


def _rb_span(src: io.BufferedReader, dst: io.FileIO, span: tuple[int, int],
             chunk: int, progress: Callable[[int, int], None] | None,
             total: int, done: int) -> tuple[str, int]:
    """比对单个区间；返回 (差异说明, 累计字节)，说明为空表示一致。"""
    start, end = span
    src.seek(start)
    dst.seek(start)
    while start < end:
        want = min(chunk, end - start)
        left = src.read(want)
        right = dst.read(want)
        note = _rb_diff(left, right, start)
        if note:
            return note, done
        start += len(left)
        done += len(left)
        if progress is not None:
            progress(done, total)
    return "", done


def _rb_ranges(image: Path, mode: str,
               window: int) -> tuple[list[tuple[int, int]], int]:
    """比对区间与对应字节数：full 整段，其余按头/中/尾抽检。"""
    size = image.stat().st_size
    ranges = [(0, size)] if mode == "full" else sample_ranges(size, window)
    return ranges, sum(end - start for start, end in ranges)


def readback(
    image: Path,
    device: Path,
    mode: str,
    *,
    chunk: int = CHUNK,
    window: int = SAMPLE_WINDOW,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[bool, str, list[tuple[int, int]]]:
    """回读比对。返回 (一致, 说明, 比对区间)。"""
    ranges, total = _rb_ranges(image, mode, window)
    done = 0
    try:
        with open(image, "rb") as src, open(device, "rb", buffering=0) as dst:
            for span in ranges:
                note, done = _rb_span(
                    src, dst, span, chunk, progress, total, done)
                if note:
                    return False, note, ranges
    except OSError as exc:
        raise FlashError(EXIT_IO, f"回读失败: {exc.strerror or exc}") from exc
    return True, f"比对 {len(ranges)} 段 / {human_bytes(total)}", ranges


# ─── 渲染 ───────────────────────────────────────────────────────────────────

TAGS = {OK: "[ OK ]", SKIP: "[SKIP]", FAIL: "[FAIL]"}

NEXT_STEPS = {
    "plan": ("预检已通过；确认目标盘后加 --yes 执行写入并回读校验",),
    "write": (
        "1. 安全弹出 U 盘，插入目标机，开机按 F12/F11/Esc 选择该 U 盘",
        "2. UEFI 固件请选带 UEFI 前缀的条目；BIOS 模式直接选 USB 设备",
        '3. 装机后核对：hostname=airymax-pc，uname -r 以 "-alk" 结尾',
        "4. 核对登录策略：amuser 本地/SSH 免密，root 口令 00000000",
    ),
}
NEXT_STEPS["check"] = (
    "1. 预检目标盘：airy-flash plan --image X.iso --device /dev/sdX",
    "2. 确认无误后写入：把 plan 换成 write，并追加 --yes",
)
NEXT_STEPS["verify"] = ("回读一致即可插机引导；不一致请重做 U 盘",)
NEXT_STEPS["fetch"] = (
    "1. 预检目标盘：airy-flash plan --image X.iso --device /dev/sdX",
    "2. 确认无误后写入：把 plan 换成 write，并追加 --yes",
)

MODES = {
    "plan": "只预检（不写设备）",
    "write": "写入 + 回读",
    "verify": "校验镜像 + 回读（不写设备）",
}


def _tag(item: Check | Stage) -> str:
    """预检项与阶段共用同一套三种标签。"""
    if isinstance(item, Stage):
        return TAGS.get(item.status, TAGS[FAIL])
    return TAGS[SKIP if item.skipped else (OK if item.ok else FAIL)]


def _label(stage: Stage) -> str:
    return f"{stage.index}/{stage.total} {stage.name}"


def _marker_digest(report: Report) -> str:
    """标记行的 sha256：优先实算值，其次映像声明的期望值。"""
    if not report.image:
        return ""
    return report.image.digest or report.image.expected


def _marker_facts(report: Report) -> list[str]:
    """标记行的已有事实：check 与早期失败不该出现空字段。"""
    parts = [f"command={report.command}", f"host={report.host}"]
    if report.image:
        parts.append(f"image={report.image.path}")
    digest = _marker_digest(report)
    if digest:
        parts.append(f"sha256={digest}")
    if report.device:
        parts.append(f"device={report.device.path}")
    return parts


class Renderer:
    """人读输出的唯一来源：预检项、阶段与摘要共用一套列宽。

    TTY 走进度条，非 TTY 走逐行里程碑，两者都无 ANSI 污染日志。
    """

    WIDTH = 68
    LABEL = 6       # 头部字段标签列
    NAME = 16       # 预检项与阶段名所在列
    MILESTONES = 4  # 非 TTY 进度粒度：四等分足以判断是否卡住
    BEAT = 5.0      # 长阻塞阶段的心跳间隔（秒）

    def __init__(
        self, stream=None, tty: bool | None = None, pulse: bool = True
    ) -> None:
        self.stream = stream or sys.stdout
        self.tty = self.stream.isatty() if tty is None else tty
        self.pulse = pulse
        self._lock = threading.Lock()
        self._bar_open = False
        self._last_bucket = -1
        self._last_emit = 0.0
        self._current = Stage(0, 0, "", OK)

    def _write(self, text: str) -> None:
        # 心跳线程与主线程共用一条流，整行加锁避免交错。
        with self._lock:
            self.stream.write(text)
            self.stream.flush()

    def rule(self, char: str = "-") -> None:
        self._write(char * self.WIDTH + "\n")

    def title(self, text: str) -> None:
        self._write(f"{text}\n")
        self.rule("=")

    def field(self, label: str, value: str, extra: str = "") -> None:
        # 标签按 LABEL 列补齐后接一个空格，故续行缩进固定 LABEL+1 列。
        self._write(f"{pad(label, self.LABEL)} {value}\n")
        if extra:
            self._write(" " * (self.LABEL + 1) + f"{extra}\n")

    def section(self, name: str) -> None:
        self._write(f"\n{name}\n")

    def row(self, tag: str, name: str, detail: str = "") -> None:
        """预检项与阶段共用的唯一行格式：标签、定宽名称、说明。"""
        self._write(f"  {tag} {pad(name, self.NAME)} {detail}\n")

    def check(self, check: Check) -> None:
        self.row(_tag(check), check.name, check.detail)

    def stage_start(self, stage: Stage) -> None:
        """只登记当前阶段：进度行与结束行各自自带前缀，避免半行残留。"""
        self._current = stage
        self._last_bucket = -1
        self._last_emit = 0.0

    def _head(self, stage: Stage) -> str:
        return pad(_label(stage), self.NAME)

    def stage_progress(self, done: int, total: int, rate: float, eta: float) -> None:
        ratio = done / total if total else 1.0
        head = self._head(self._current)
        if self.tty:
            self._write(self._tty_line(head, ratio, done, total, rate, eta))
            self._bar_open = True
            return
        self._log_progress(head, ratio, done, total, rate, eta)

    def _tty_line(self, head: str, ratio: float, done: int, total: int,
                  rate: float, eta: float) -> str:
        """TTY 上的单行进度条：回车回到行首，条宽固定。"""
        width = 24
        filled = max(0, min(width, int(ratio * width)))
        bar = "#" * filled + "." * (width - filled)
        return (f"\r  [ .. ] {head} {bar} {ratio * 100:3.0f}% "
                f"{human_bytes(done)}/{human_bytes(total)} "
                f"{human_bytes(rate)}/s ETA {human_duration(eta)}")

    def _due(self, bucket: int, moment: float) -> bool:
        """同刻度内只在心跳间隔到期时补发。"""
        return (bucket != self._last_bucket
                or moment - self._last_emit >= self.BEAT)

    def _log_progress(self, head: str, ratio: float, done: int, total: int,
                      rate: float, eta: float) -> None:
        """非 TTY 的里程碑行与心跳补发。"""
        bucket = int(ratio * self.MILESTONES)
        # 100% 里程碑与紧随其后的 stage_end 结果行重复，日志里两行同义；
        # 仅保留 0/25/50/75 四个过程刻度。
        if bucket >= self.MILESTONES:
            return
        # 四等分只够分辨「跑完多少」，不够分辨「还活着还是挂住了」：3.7 GB
        # 写盘时相邻刻度相隔两分钟以上，日志里就是两分钟静默（FL 现场按
        # 日志判断卡死时踩过）。刻度仍随状态跃迁输出，另外按心跳间隔补发
        # 带读数的行——百分比、已写字节、当前速率与 ETA 都在，慢和死就不
        # 再是同一种现象。
        moment = time.monotonic()
        if not self._due(bucket, moment):
            return
        self._last_bucket = bucket
        self._last_emit = moment
        self._write(
            f"  [ .. ] {head} {ratio * 100:3.0f}% "
            f"{human_bytes(done)}/{human_bytes(total)} "
            f"{human_bytes(rate)}/s ETA {human_duration(eta)}\n"
        )

    def heartbeat(self, stage: Stage, seconds: float) -> None:
        """无进度的阻塞阶段也要让屏幕持续刷新，避免看起来像卡死。"""
        line = f"  [ .. ] {self._head(stage)} 已等待 {human_duration(seconds)}"
        if self.tty:
            self._write(f"\r{line}   ")
            self._bar_open = True
            return
        self._write(line + "\n")

    def stage_end(self, stage: Stage) -> None:
        prefix = "\r" if self._bar_open else ""
        self._write(f"{prefix}  {_tag(stage)} {self._head(stage)} {stage.detail}\n")
        self._bar_open = False
        self._last_bucket = -1

    def stages_header(self) -> None:
        self.section("阶段")

    def checks_header(self) -> None:
        self.section("预检")

    def summary(self, report: Report, elapsed: float, report_path: str = "") -> None:
        # 字段块与头部用同一个 field()：两处各写一套对齐会差一列。
        self.section("结果")
        mark = "成功" if report.result == "pass" else "失败"
        self.field("判定", mark)
        self.field("用时", human_duration(elapsed))
        self._opt_fields(report)
        for line in report.warnings:
            self.field("提示", line)
        if report_path:
            self.field("报告", report_path)

    def _opt_fields(self, report: Report) -> None:
        """原因与建议只在存在时输出。"""
        if report.error:
            self.field("原因", report.error)
        if report.hint:
            self.field("建议", report.hint)

    def marker(self, report: Report) -> None:
        status = "PASS" if report.result == "pass" else "FAIL"
        self._write(f"\nAIRY-FLASH-{status} " + " ".join(_marker_facts(report)) + "\n")

    def next_steps(self, command: str) -> None:
        """按子命令给出可执行的下一步，避免 plan 成功被误当成已写入。"""
        self.section("下一步")
        for line in NEXT_STEPS.get(command, NEXT_STEPS["write"]):
            self._write(f"  {line}\n")


class Ticker:
    """长阻塞阶段的屏幕心跳：只在超过间隔没有进展时补一行。"""

    def __init__(
        self, renderer: Renderer, stage: Stage, period: float | None = None
    ) -> None:
        self.renderer = renderer
        self.stage = stage
        self.period = renderer.BEAT if period is None else period
        self._stop = threading.Event()
        self._last = time.monotonic()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        self._last = time.monotonic()

    def __enter__(self) -> Ticker:
        if self.renderer.pulse:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(min(self.period, 0.5)):
            waited = time.monotonic() - self._last
            if waited >= self.period:
                self.renderer.heartbeat(self.stage, waited)
                self.touch()

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.period)


Progress = Callable[[int, int], None]
Task = Callable[[Progress], tuple[str, str]]


def _meter(started: float) -> Callable[[int, int], tuple[float, float]]:
    """返回 (已完成, 总量) → (瞬时速率, 剩余秒数) 的换算器。"""

    def compute(done: int, total: int) -> tuple[float, float]:
        elapsed = max(time.monotonic() - started, 1e-6)
        rate = done / elapsed
        remaining = (total - done) / rate if rate > 0 else 0.0
        return rate, remaining

    return compute


def _progress(
    renderer: Renderer, tick: Ticker, meter: Callable[[int, int], tuple[float, float]]
) -> Progress:
    """把「进度回调」与「心跳续命」合成一个回调：有进展就不补心跳。"""

    def advance(done: int, total: int) -> None:
        tick.touch()
        rate, eta = meter(done, total)
        renderer.stage_progress(done, total, rate, eta)

    return advance


# ─── 编排 ───────────────────────────────────────────────────────────────────


def _report_of(args: argparse.Namespace) -> Report:
    return Report(
        command=args.command,
        result="fail",
        started=now_iso(),
        argv=list(sys.argv) if args.argv is None else list(args.argv),
        verify=args.verify,
        host=socket.gethostname(),
    )


def _renderer_of(args: argparse.Namespace) -> Renderer:
    """--json 与 --no-progress 都关掉 TTY 渲染，两者只差日志去向。"""
    quiet = args.no_progress or args.json
    return Renderer(
        stream=io.StringIO() if args.json else None,
        tty=False if quiet else None,
        pulse=not quiet,
    )


def _dispatch(args: argparse.Namespace, report: Report, renderer: Renderer) -> int:
    try:
        if args.command == "fetch":
            _run_fetch(args, report, renderer)
        else:
            _run(args, report, renderer)
    except FlashError as exc:
        report.error, report.hint = exc.message, exc.hint
        return exc.code
    return EXIT_OK


def execute(args: argparse.Namespace) -> tuple[Report, int]:
    """唯一入口：标题先出，成败都走同一套七段与标记。"""
    report = _report_of(args)
    renderer = _renderer_of(args)
    clock = time.monotonic()
    renderer.title(f"AirymaxOS 烧录工具 {TOOL_VERSION}  ({args.command})")
    code = _dispatch(args, report, renderer)
    return _close(args, report, renderer, code, clock)


def _run(args: argparse.Namespace, report: Report, renderer: Renderer) -> None:
    image = probe_image(Path(args.image))
    expected, source = resolve_exp(
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
    _banner(image, device, args.command, renderer)
    _guard(args, device)
    _preflight(args, report, renderer, image, device, expected, source)

    _confirm(args, device)

    renderer.stages_header()
    steps = _steps(args, report, image, device, expected)
    for index, (name, body) in enumerate(steps, 1):
        _stage(renderer, report, index, len(steps), name, body)


def _steps(
    args: argparse.Namespace,
    report: Report,
    image: ImageFacts,
    device: DeviceFacts,
    expected: str,
) -> list[tuple[str, Task]]:
    """按子命令排出阶段序列：plan 只校镜像，write 四段，verify 校镜像 + 回读。"""
    steps: list[tuple[str, Task]] = [
        ("校验镜像", lambda task: _hash(image, report, expected, task)),
    ]
    if args.command == "write":
        steps.append(("写入设备", lambda task: _write(image, device, task)))
        steps.append(("刷盘同步", lambda _task: _flush(device)))
    if args.command != "plan":
        steps.append(
            ("回读校验", lambda task: _readback(image, device, args.verify, task))
        )
    return steps


def _stage(
    renderer: Renderer,
    report: Report,
    index: int,
    total: int,
    name: str,
    body: Task,
) -> None:
    """阶段的唯一执行壳：起点、心跳、进度、结束行与报告都从这里走。"""
    stage = Stage(index, total, name, FAIL)
    renderer.stage_start(stage)
    clock = time.monotonic()
    with Ticker(renderer, stage) as tick:
        advance = _progress(renderer, tick, _meter(clock))
        try:
            status, detail = body(advance)
        except FlashError as exc:
            _end(renderer, report, stage, FAIL, exc.message)
            raise
        except OSError as exc:
            message = f"{name}失败: {exc.strerror or exc}"
            _end(renderer, report, stage, FAIL, message)
            raise FlashError(EXIT_IO, message) from exc
    if status == OK:
        detail = f"{detail}  {human_duration(time.monotonic() - clock)}"
    _end(renderer, report, stage, status, detail)


def _end(
    renderer: Renderer, report: Report, stage: Stage, status: str, detail: str
) -> None:
    """阶段收尾的唯一出口：落一行、进报告，失败与成功同形。"""
    done = Stage(stage.index, stage.total, stage.name, status, detail.strip())
    renderer.stage_end(done)
    report.stages.append(done)


def _target_facts(device: DeviceFacts) -> str:
    """目标盘的一行事实：型号、总线、容量、可移动与备注，空项略过。"""
    parts = (
        device.model,
        device.tran,
        human_bytes(device.size),
        "可移动" if device.removable else "",
        device.note,
    )
    return ", ".join(part for part in parts if part)


def _parts_line(device: DeviceFacts) -> str:
    """分区清单的前六项；超过六项时附带总数，避免一行过长。"""
    listed = ", ".join(device.parts[:6])
    if len(device.parts) > 6:
        listed = f"{listed} 等 {len(device.parts)} 个"
    return listed


def _banner(
    image: ImageFacts, device: DeviceFacts, command: str, renderer: Renderer
) -> None:
    """三行身份信息：映像、目标、模式，是后面所有判定的上下文。"""
    digest = short_digest(image.expected) if image.expected else "（未提供）"
    renderer.field(
        "映像", str(image.path), f"{human_bytes(image.size)}   sha256 {digest}"
    )
    renderer.field("目标", str(device.path), _target_facts(device))
    # 分区清单是「这块盘上现在有什么」的唯一可见事实：写入连同分区表一起
    # 覆盖，事后无法回溯，事前又只能从盘符反推。
    if device.parts:
        renderer.field(
            "分区",
            f"已有 {len(device.parts)} 个 {_parts_line(device)}",
            f"合计 {human_bytes(device.parts_bytes)}，写入后不可恢复",
        )
    renderer.field("模式", MODES.get(command, command))


def _guard(args: argparse.Namespace, device: DeviceFacts) -> None:
    """写块设备必须 root：预检之前就拦下，避免探测通过后写入才失败。"""
    if device.kind != "block" or args.command == "plan" or os.geteuid() == 0:
        return
    raise FlashError(
        EXIT_USAGE,
        "写块设备需要 root：请用 sudo 重新执行",
        hint="sudo airy-flash write --image X.iso --device /dev/sdX --yes",
    )


def _confirm(args: argparse.Namespace, device: DeviceFacts) -> None:
    """覆盖整块设备要显式 --yes；放在预检之后，操作者已看清目标身份。"""
    if args.command != "write" or device.kind != "block" or args.yes:
        return
    raise FlashError(
        EXIT_USAGE,
        f"将覆盖 {device.path}（{human_bytes(device.size)}）上的全部数据；"
        "确认请加 --yes",
        hint="确认目标盘无误后追加 --yes 重新执行",
    )


def _preflight(
    args: argparse.Namespace,
    report: Report,
    renderer: Renderer,
    image: ImageFacts,
    device: DeviceFacts,
    expected: str,
    source: str,
) -> None:
    """先渲染逐条判定，再对失败项抛错：预检未过即不触碰设备。"""
    checks = preflight(
        image,
        device,
        expected=expected,
        source=source,
        allow_unverified=args.allow_unverified,
    )
    _verdict(report, renderer, checks, "预检未通过，未触碰目标设备")


def _advice(failed: list[Check]) -> str:
    """把失败项的处置动作拼成一句建议；没有 fix 的条目只报条目名。

    单条失败时不带条目前缀——「关闭写保护开关」比「非只读：关闭写保护
    开关」更符合现场语气，而多条并列时前缀是必要的分隔。
    """
    if len(failed) == 1:
        return failed[0].fix or failed[0].name
    return "；".join(f"{c.name}：{c.fix}" if c.fix else c.name for c in failed)


def _failed(checks: list[Check]) -> list[Check]:
    """真正失败的条目：跳过项不算失败，但仍留在逐条渲染里。"""
    return [c for c in checks if not c.ok and not c.skipped]


def _verdict(
    report: Report,
    renderer: Renderer,
    checks: list[Check],
    block: str,
    hint: str = "",
) -> None:
    """预检的公共收尾：逐条渲染后统一判死，两种子命令共用一套输出。"""
    report.checks = checks
    renderer.checks_header()
    for check in checks:
        renderer.check(check)
    failed = _failed(checks)
    if failed:
        raise FlashError(EXIT_CHECK, block, hint or _advice(failed))


def _hash(
    image: ImageFacts, report: Report, expected: str, task: Progress
) -> tuple[str, str]:
    """校验镜像摘要；不符即抛错，实际摘要无论如何都写进报告。"""
    if not expected:
        return SKIP, "未提供摘要，跳过（--allow-unverified）"
    digest = sha256_file(image.path, progress=task)
    report.image = ImageFacts(
        path=image.path,
        size=image.size,
        digest=digest,
        expected=expected,
        sums_file=image.sums_file,
    )
    if digest == expected:
        return OK, f"sha256 一致 {short_digest(digest)}"
    raise FlashError(
        EXIT_CHECK,
        f"摘要不符：实际 {short_digest(digest)} ≠ 期望 {short_digest(expected)}",
        hint="重新下载镜像；或核对发布 SHA256SUMS 与本地文件是否同一份",
    )


def _write(image: ImageFacts, device: DeviceFacts, task: Progress) -> tuple[str, str]:
    clock = time.monotonic()
    written = write_image(
        image.path, device.path, progress=task, file_target=device.kind == "file"
    )
    span = max(time.monotonic() - clock, 1e-6)
    return OK, f"{human_bytes(written)}  {human_bytes(written / span)}/s"


def _flush(device: DeviceFacts) -> tuple[str, str]:
    flush_device(device.path)
    return OK, "fsync 完成"


def _readback(
    image: ImageFacts, device: DeviceFacts, mode: str, task: Progress
) -> tuple[str, str]:
    if mode == "none":
        return SKIP, "--verify none 已跳过回读"
    same, detail, _ranges = readback(image.path, device.path, mode, progress=task)
    if same:
        return OK, detail
    raise FlashError(
        EXIT_IO,
        detail,
        hint="换数据线或换 USB 口重试；仍不一致则重做 U 盘",
    )


# ─── 取回（OCI 分块） ───────────────────────────────────────────────────────


def _offset(task: Progress, base: int, total: int) -> Callable[[int, int], None]:
    """把单块进度换算成整份镜像的进度，避免进度条在块边界回跳。"""

    def advance(done: int, _size: int) -> None:
        task(base + done, total)

    return advance


def _clear_parts(ledger: OciLedger, out: Path) -> None:
    """先清掉上一轮的残留块：oras 只补缺失的层，旧块留着会掩盖清单变化。"""
    for part in ledger.parts:
        (out / part.name).unlink(missing_ok=True)


def _pull_run(ledger: OciLedger, out: Path, oras: str) -> None:
    proc = subprocess.run(
        [oras, "pull", ledger.target, "-o", str(out)],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    detail = tail[-1] if tail else f"退出 {proc.returncode}"
    raise FlashError(
        EXIT_IO,
        f"oras pull 失败: {detail}",
        hint="仓库或网络不可达；换镜像站后重试同一条命令",
    )


def _missing_parts(ledger: OciLedger, out: Path) -> list[str]:
    return [part.name for part in ledger.parts if not (out / part.name).is_file()]


def _pull_parts(ledger: OciLedger, out: Path, oras: str) -> tuple[str, str]:
    """一次 oras pull 取回全部层；齐不齐看清单，不看 oras 的措辞。"""
    _clear_parts(ledger, out)
    _pull_run(ledger, out, oras)
    missing = _missing_parts(ledger, out)
    if missing:
        raise FlashError(
            EXIT_CHECK,
            f"缺少 {len(missing)} 块（首块 {missing[0]}）",
            hint="发布物与清单不一致：重新取 iso-oci.txt 后重试",
        )
    return OK, f"{ledger.chunks} 块 {human_bytes(ledger.size)}"


def _check_parts(ledger: OciLedger, out: Path, task: Progress) -> tuple[str, str]:
    """逐块核对：字节数与 sha256 都对得上，才允许拼接。"""
    done = 0
    for part in ledger.parts:
        path = out / part.name
        if path.stat().st_size != part.size:
            raise FlashError(
                EXIT_CHECK,
                f"第 {part.index} 块字节数不符",
                hint=f"删除 {path.name} 后重跑 fetch",
            )
        if sha256_file(path, progress=_offset(task, done, ledger.size)) != part.digest:
            raise FlashError(
                EXIT_CHECK,
                f"第 {part.index} 块 sha256 不符",
                hint=f"删除 {path.name} 后重跑 fetch",
            )
        done += part.size
    return OK, f"{ledger.chunks} 块逐一一致"


def _join_parts(ledger: OciLedger, dest: Path, task: Progress) -> tuple[str, str]:
    """按序拼接，边拼边删已用完的块：峰值只多占一个块，不是又一份镜像。"""
    done = 0
    with dest.open("wb") as sink:
        for part in ledger.parts:
            source = dest.parent / part.name
            with source.open("rb") as src:
                while True:
                    buf = src.read(CHUNK)
                    if not buf:
                        break
                    sink.write(buf)
                    done += len(buf)
                    task(done, ledger.size)
            source.unlink()
        os.fsync(sink.fileno())
    return OK, f"{ledger.chunks} 块拼接完成"


def _even_parts(ledger: OciLedger) -> bool:
    """除末块外都等于 chunk_size，末块在 1..chunk_size 之间。"""
    sizes = [part.size for part in ledger.parts]
    if not all(size == ledger.chunk_size for size in sizes[:-1]):
        return False
    return 0 < sizes[-1] <= ledger.chunk_size


def _oras_check(oras: str, fetched: bool) -> Check:
    if fetched:
        return Check("oras", True, "已取回，本次不需要", skipped=True)
    located = shutil.which(oras)
    return Check("oras", bool(located), located or f"{oras} 不在 PATH 中")


def _room_check(ledger: OciLedger, out: Path, fetched: bool) -> Check:
    free = shutil.disk_usage(out).free
    need = ledger.size + ledger.chunk_size + SPACE_SLACK
    if fetched:
        return Check("磁盘余量", True, f"可用 {human_bytes(free)}", skipped=True)
    return Check(
        "磁盘余量", free >= need, f"可用 {human_bytes(free)}，需要 {human_bytes(need)}"
    )


def _shape_check(ledger: OciLedger) -> Check:
    even = _even_parts(ledger)
    return Check(
        "分块尺寸",
        even,
        f"{ledger.chunks} 块 x {human_bytes(ledger.chunk_size)}"
        if even
        else "块尺寸与清单的 chunk_size 不一致",
    )


def _fetch_checks(
    ledger: OciLedger, out: Path, oras: str, fetched: bool
) -> list[Check]:
    """取回前的三条判定：oras 在位、目录余量够、分块尺寸自洽。"""
    return [
        _oras_check(oras, fetched),
        _room_check(ledger, out, fetched),
        _shape_check(ledger),
    ]


def _banner_fetch(
    ledger: OciLedger, source: str, dest: Path, renderer: Renderer, fetched: bool
) -> None:
    """取回的三行身份：清单、来源、目标，与 write 的头部一一对应。"""
    renderer.field(
        "清单",
        source,
        f"{ledger.chunks} 块 x {human_bytes(ledger.chunk_size)}"
        f"  sha256 {short_digest(ledger.sha256)}",
    )
    renderer.field("来源", ledger.target, human_bytes(ledger.size))
    renderer.field(
        "目标",
        str(dest),
        "已存在且字节数一致，只做校验" if fetched else "待取回",
    )


def _fetch_steps(
    report: Report,
    image: ImageFacts,
    ledger: OciLedger,
    dest: Path,
    fetched: bool,
    oras: str,
) -> list[tuple[str, Task]]:
    """已取回只走校验；否则拉取、逐块核对、拼接、整体核对四段。"""
    if fetched:
        return [
            ("校验镜像", lambda task: _hash(image, report, ledger.sha256, task)),
        ]
    return [
        ("拉取分块", lambda _task: _pull_parts(ledger, dest.parent, oras)),
        ("校验分块", lambda task: _check_parts(ledger, dest.parent, task)),
        ("拼接镜像", lambda task: _join_parts(ledger, dest, task)),
        ("校验镜像", lambda task: _hash(image, report, ledger.sha256, task)),
    ]


def _ledger_from(ref: str, oras: str) -> tuple[OciLedger, str]:
    """先取 <tag>-ledger 制品里的 iso-oci.txt，未命中才回落清单注解。

    清单注解是发布侧写死的元数据，用户若按它手工重建块表，读错一处注解
    不会有任何提示；发布侧把 iso-oci.txt 作为单层小制品推在同一 tag 的
    -ledger 后缀下，取件侧优先拿它。制品不存在时（早于该做法的发布物）
    回落清单重建，并在报告里明确标出「未命中」，不让降级路径看起来像
    正常路径。
    """
    with tempfile.TemporaryDirectory(prefix="airy-ledger.") as tmp:
        try:
            proc = subprocess.run(
                [oras, "pull", f"{ref}-ledger", "-o", tmp],
                capture_output=True,
                text=True,
            )
        except OSError:
            proc = None
        path = Path(tmp) / "iso-oci.txt"
        if proc is not None and proc.returncode == 0 and path.is_file():
            return parse_oci(path), f"{ref}-ledger（账本命中）"
    return ledger_from_ref(ref, oras), f"{ref}（账本未命中，按清单注解重建）"


def _ledger_arg(args: argparse.Namespace) -> tuple[OciLedger, str]:
    """两个取件入口二选一：账本文件可离线复盘，--ref 免去文件分发。"""
    if bool(args.oci) == bool(args.ref):
        raise FlashError(
            EXIT_USAGE,
            "--oci 与 --ref 必须且只能给一个",
            hint="有账本用 --oci FILE；只知 tag 用 "
            "--ref ghcr.io/openairymax/airymaxos-iso:v0.0.3",
        )
    if args.oci:
        return parse_oci(Path(args.oci)), str(Path(args.oci))
    return _ledger_from(args.ref, args.oras)


def _run_fetch(
    args: argparse.Namespace, report: Report, renderer: Renderer
) -> None:
    """取回：清单 → 预检 → 分段执行，收尾与 write 走同一条路径。"""
    ledger, source = _ledger_arg(args)
    report.oci = ledger
    out = Path(args.out)
    _ensure_dir(out)
    dest = out / ledger.name
    fetched = _cached(args, dest, ledger)
    image = ImageFacts(
        path=dest, size=ledger.size, expected=ledger.sha256, sums_file=source
    )
    report.image = image
    _banner_fetch(ledger, source, dest, renderer, fetched)
    _verdict(
        report,
        renderer,
        _fetch_checks(ledger, out, args.oras, fetched),
        "预检未通过，未取回任何数据",
    )
    renderer.stages_header()
    steps = _fetch_steps(report, image, ledger, dest, fetched, args.oras)
    for index, (name, body) in enumerate(steps, 1):
        _stage(renderer, report, index, len(steps), name, body)


def _ensure_dir(out: Path) -> None:
    """输出目录不存在就建；失败按用法错误报出。"""
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FlashError(
            EXIT_USAGE, f"建不了输出目录 {out}: {exc.strerror or exc}"
        ) from exc


def _cached(args: argparse.Namespace, dest: Path, ledger) -> bool:
    """非 --force 且已存在同尺寸文件时视为已取回。"""
    if args.force or not dest.is_file():
        return False
    return dest.stat().st_size == ledger.size


def _write_report(args: argparse.Namespace, report: Report) -> str:
    """落报告并返回路径；写入失败只记警告，不改变本次结论。"""
    if not args.report:
        return ""
    path = str(Path(args.report))
    try:
        write_report(report, Path(args.report))
    except OSError as exc:
        report.warnings.append(f"报告写入失败: {exc.strerror or exc}")
        return ""
    return path


def _close(
    args: argparse.Namespace,
    report: Report,
    renderer: Renderer,
    code: int,
    clock: float,
) -> tuple[Report, int]:
    """成功与失败的唯一收尾：落报告、摘要、下一步与标记。"""
    report.result = "pass" if code == EXIT_OK else "fail"
    report.finished = now_iso()
    path = _write_report(args, report)
    renderer.summary(report, time.monotonic() - clock, path)
    if code == EXIT_OK and args.command in NEXT_STEPS:
        renderer.next_steps(args.command)
    # 标记行放最后：日志尾部一行即可取到发布物与设备身份。
    renderer.marker(report)
    return report, code


def _head_rows(report: Report) -> list[str]:
    """身份字段：键序固定，缺值留空，读日志的人不必查找字段在哪。"""
    fields = (
        ("result", report.result),
        ("command", report.command),
        ("host", report.host),
        ("started", report.started),
        ("finished", report.finished),
        ("verify", report.verify),
        ("argv", " ".join(report.argv)),
        ("error", report.error),
        ("hint", report.hint),
    )
    return [f"{key}={value}" for key, value in fields]


def _image_rows(image: ImageFacts) -> list[str]:
    """映像段：实际摘要与期望摘要并排，便于事后判断校验是否生效。"""
    return [
        f"image.path={image.path}",
        f"image.bytes={image.size}",
        f"image.sha256={image.digest}",
        f"image.sha256.expected={image.expected}",
        f"image.sha256.source={image.sums_file}",
    ]


def _device_rows(device: DeviceFacts) -> list[str]:
    """目标设备段：型号与序列号是事后追查「写到了哪个盘」的唯一凭据。"""
    return [
        f"device.path={device.path}",
        f"device.kind={device.kind}",
        f"device.bytes={device.size}",
        f"device.model={device.model}",
        f"device.serial={device.serial}",
    ]


def _oci_rows(oci: OciLedger) -> list[str]:
    """发布清单段：逐块留痕，缺块或错块能直接定位到层。"""
    rows = [
        f"oci.target={oci.target}",
        f"oci.name={oci.name}",
        f"oci.sha256={oci.sha256}",
        f"oci.chunks={oci.chunks}",
        f"oci.chunk_bytes={oci.chunk_size}",
    ]
    rows.extend(
        f"oci.part.{part.index}={part.digest} {part.size} {part.name}"
        for part in oci.parts
    )
    return rows


def _opt_rows(report: Report) -> list[str]:
    """可选段：本次未涉及的映像/设备/清单整段不出现，不做空占位。"""
    rows: list[str] = []
    if report.image:
        rows.extend(_image_rows(report.image))
    if report.device:
        rows.extend(_device_rows(report.device))
    if report.oci:
        rows.extend(_oci_rows(report.oci))
    return rows


def _check_row(check: Check) -> str:
    """预检项：skipped 优先于 ok，因为跳过项的 ok 值没有意义。"""
    state = SKIP if check.skipped else ("pass" if check.ok else "fail")
    return f"check.{check.name}={state}"


def _stage_row(stage: Stage) -> str:
    """阶段行：保留耗时明细，卡住时据此判断停在哪个阶段。"""
    return f"stage.{stage.index}.{stage.name}={stage.status} ({stage.detail})"


def _write_json(payload: dict, path: Path) -> None:
    """结构化报告：UTF-8 原样落盘，保留中文说明。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def write_report(report: Report, path: Path) -> None:
    """落盘报告：.json 走结构化，其余走 key=value 行文本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        _write_json(report.as_dict(TOOL_VERSION), path)
        return
    rows: list[str] = [f"airymax-flash-report v1 (airy-flash {TOOL_VERSION})"]
    rows.extend(_head_rows(report))
    rows.extend(_opt_rows(report))
    rows.extend(_check_row(check) for check in report.checks)
    rows.extend(_stage_row(stage) for stage in report.stages)
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
        choices=("fetch", "plan", "write", "verify", "check"),
        help="fetch 取回分块；plan 只预检（默认）；write 写入；"
        "verify 校验镜像并回读；check 环境自检",
    )
    parser.add_argument("--image", help="ISO 或 raw 镜像路径")
    parser.add_argument("--oci", help="fetch：发布清单 iso-oci.txt 路径")
    parser.add_argument(
        "--ref",
        help="fetch：OCI 引用（如 ghcr.io/openairymax/airymaxos-iso:v0.0.3），"
        "清单本身即账本，无需外部文件",
    )
    parser.add_argument("--out", default=".", help="fetch：输出目录（默认当前目录）")
    parser.add_argument(
        "--force", action="store_true", help="fetch：忽略本地已存在的镜像，重新取回"
    )
    parser.add_argument(
        "--oras", default="oras", help="fetch：oras 可执行文件（默认取自 PATH）"
    )
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


def _env_checks() -> list[Check]:
    """环境自检四项：解释器、两个内核接口、root 权限。"""
    mounts, sysfs = Path("/proc/mounts"), Path("/sys/class/block")
    root = os.geteuid() == 0
    return [
        Check("python", True, sys.version.split()[0]),
        Check("/proc/mounts", mounts.exists(), "可读" if mounts.exists() else "缺失"),
        Check("/sys/class/block", sysfs.is_dir(), "存在" if sysfs.is_dir() else "缺失"),
        Check(
            "root",
            root,
            "是" if root else "否（写块设备时需 sudo）",
            skipped=not root,
        ),
    ]


def _check(args: argparse.Namespace) -> tuple[Report, int]:
    """环境自检：与烧录共用同一个 Report 与渲染器，失败也落报告。"""
    report = Report(
        command="check",
        result="pass",
        started=now_iso(),
        argv=list(sys.argv) if args.argv is None else list(args.argv),
        host=socket.gethostname(),
    )
    report.checks = _env_checks()
    renderer = _renderer_of(args)
    clock = time.monotonic()
    renderer.title(f"AirymaxOS 烧录工具 {TOOL_VERSION}  (check)")
    renderer.checks_header()
    for item in report.checks:
        renderer.check(item)
    return _close(args, report, renderer, EXIT_OK, clock)


def _route(parser: argparse.ArgumentParser,
           args: argparse.Namespace) -> tuple[Report, int]:
    """按子命令分派：check 走自检，其余交给烧录执行器。"""
    if args.command == "check":
        return _check(args)
    # fetch 的两个取件入口（--oci / --ref）由 _ledger_arg 统一判定，
    # 这里再拦一次只会让 --ref 永远走不到实现：清单本身就是账本，
    # 发布制品过期后仍能按 tag 取回，这条路径不能只在源码里存在。
    if args.command != "fetch" and not (args.image and args.device):
        parser.error("需要 --image 与 --device（check/fetch 子命令除外）")
    return execute(args)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.argv = list(argv) if argv is not None else None
    report, code = _route(parser, args)
    if args.json:
        sys.stdout.write(
            json.dumps(report.as_dict(TOOL_VERSION), ensure_ascii=False, indent=2) + "\n"
        )
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
