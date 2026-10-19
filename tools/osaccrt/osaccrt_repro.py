# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_repro — 已封口证据包的复现脚本生成器。

归档步要求第三方仅凭证据包即可重放同一实验。本模块把清单中的
实验编号、宿主、镜像摘要、阈值表、判定与链头写入一段自包含 POSIX
sh 脚本：脚本先逐条复核证据摘要与链头，再按需执行原始命令。校验
失败即拒绝复现，因此脚本本身也是证据完整性的离线检查器。

脚本不读取任何非标准输入，只依赖 ``sha256sum`` 与 ``mktemp``；
重放命令在临时目录内执行，避免污染只读证据包。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §1.3 完成定义、§3.1 六步规程第 6 步、§3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-07 复现脚本生成器）

Example:
    >>> text = render(man, "qemu-system-x86_64 -m 2048")
    >>> text.startswith("#!/bin/sh")
    True

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Final

from osaccrt_evidence import Manifest, dump
from osaccrt_model import Threshold, need_relpath, need_text
from osaccrt_store import MANIFEST

__all__ = ["emit", "render"]

_DEFAULT_ROOT: Final[str] = "evidence"
_ROOT_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._/-]+")

_HEAD: Final[str] = """#!/bin/sh
# AirymaxOS OS-ACC-RT replay script for a sealed evidence pack.
# Generated file: regenerate from the manifest instead of editing.
#
"""

_BODY: Final[str] = """set -eu

PACK='%(default)s'
RUN=0

for arg in "$@"; do
    case $arg in
    --run)
        RUN=1
        ;;
    -h|--help)
        echo "usage: $0 [pack-dir] [--run]" >&2
        exit 2
        ;;
    -*)
        echo "unknown option: $arg" >&2
        exit 2
        ;;
    *)
        PACK=$arg
        ;;
    esac
done

if ! command -v sha256sum >/dev/null 2>&1; then
    echo "sha256sum is required" >&2
    exit 2
fi
if [ ! -d "$PACK" ]; then
    echo "evidence pack not found: $PACK" >&2
    exit 1
fi

echo "verifying evidence in $PACK"
(
    cd "$PACK"
    sha256sum -c <<'SUMS'
%(sums)sSUMS
)

chain=$(tr -d '\\n' < "$PACK/chain.sha256")
if [ "$chain" != "%(chain)s" ]; then
    echo "chain head mismatch: $chain" >&2
    exit 1
fi

echo "evidence pack verified: %(exp_id)s"

if [ "$RUN" -eq 0 ]; then
    echo "pass --run to replay the original command"
    exit 0
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/osaccrt-replay.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
cd "$work"
echo "replaying in $work"
%(command)s
"""


def render(man: Manifest, command: str,
           pack_root: str = _DEFAULT_ROOT) -> str:
    """生成复现脚本文本；未判定或字段非法一律拒绝。"""
    if not isinstance(man, Manifest):
        raise TypeError("manifest must be a Manifest")
    if man.result.verdict is None:
        raise ValueError("cannot replay an undecided evidence pack")
    default = _pack_path(pack_root, man)
    return _HEAD + _notes(man, default) + _BODY % {
        "default": default,
        "sums": _sums(man),
        "chain": man.chain_head,
        "exp_id": man.exp_id,
        "command": _line(command, "command"),
    }


def emit(man: Manifest, command: str, out: Path | str,
         pack_root: str = _DEFAULT_ROOT) -> Path:
    """原子写出可执行脚本；先写临时文件再改名，避免半成品。"""
    target = Path(out)
    text = render(man, command, pack_root)
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def _notes(man: Manifest, default: str) -> str:
    """生成脚本头部的实验元数据注释块。"""
    result = man.result
    rows = [
        f"# exp-id: {man.exp_id}",
        f"# date: {man.date}",
        f"# host: {_line(man.host, 'host')}",
        f"# image-sha256: {man.image_sha256}",
        f"# verdict: {result.verdict.value}",
        f"# chain-head: {man.chain_head}",
    ]
    if result.waiver_sig is not None:
        first, second = result.waiver_sig
        rows.append(
            f"# waiver-sig: {_line(first, 'waiver_sig')}, "
            f"{_line(second, 'waiver_sig')}"
        )
    if result.blocked_reason:
        reason = _line(result.blocked_reason, "blocked_reason")
        rows.append(f"# blocked-reason: {reason}")
    rows.extend(
        f"# threshold: {_threshold(item)}" for item in result.thresholds
    )
    rows.append("#")
    rows.append("# usage: sh <script> [pack-dir] [--run]")
    rows.append(f"# pack-dir defaults to {default}")
    return "\n".join(rows) + "\n\n"


def _threshold(item: Threshold) -> str:
    """单条阈值渲染为一行注释文本。"""
    key = _line(item.key, "key")
    limit = _line(item.limit, "limit")
    unit = _line(item.unit, "unit")
    source = _line(item.source, "source")
    return f"{key}={limit} {unit} ({source})"


def _pack_path(root: str, man: Manifest) -> str:
    """证据包默认路径；根目录限定为路径安全字符。"""
    if not isinstance(root, str) or not _ROOT_RE.fullmatch(root):
        raise ValueError(f"pack_root must be a relative path: {root!r}")
    try:
        need_relpath(root)
    except ValueError as exc:
        raise ValueError(
            f"pack_root must be a relative path: {root!r}"
        ) from exc
    path = f"{root}/{man.date}/{man.exp_id}"
    need_relpath(path)
    return path


def _sums(man: Manifest) -> str:
    """清单自身的摘要加上全部证据摘要，供 sha256sum 复核。"""
    rows = [f"{_digest(dump(man))}  {MANIFEST}"]
    rows.extend(
        f"{item.sha256}  {item.path}" for item in man.result.evidences
    )
    return "\n".join(rows) + "\n"


def _digest(text: str) -> str:
    """按 UTF-8 计算文本摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line(value: str, name: str) -> str:
    """校验注释与命令为单行可见文本，杜绝折行与注入。"""
    need_text(value, name)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{name} must be a single visible line")
    return value
