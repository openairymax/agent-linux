# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_store — append-only 证据库存储引擎。

证据目录是 OS-ACC-RT 的唯一裁决输入，必须同时满足两条硬约束：写入
只增不改；全项目哈希链单链延续。本模块把目录创建、证据落盘、封口冻结
与跨包链校验集中到一处，杜绝执行器各自拼路径造成旁路写入。

落盘采用"同目录临时文件 + link"：目标已存在则 link 失败，天然拒绝
覆盖。封口时逐条复核 sha256，再把整个证据包转为只读；跨包封口在根
目录排他锁内完成，前链头必须等于当前链头，避免并发分叉。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.1 六步规程、§3.4 证据包规范
    docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md
        §6.9.1 模块 V（TV-01-03 append-only 证据库）

Example:
    >>> store = Store(Path("evidence"))
    >>> store.create("2026-01-19", "exp-001")
    PosixPath('evidence/2026-01-19/exp-001')
    >>> len(store.write("2026-01-19", "exp-001", "step-02-run.log", b"ok\n"))
    64

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from osaccrt_evidence import STEP_LAYOUT, Manifest, check_date, dump, verify
from osaccrt_evidence import load as load_manifest
from osaccrt_evidence import seal as seal_manifest
from osaccrt_model import EXP_ID_RE, Evidence, Step, need_relpath

__all__ = ["Store"]

MANIFEST: Final[str] = "manifest.json"
CHAIN: Final[str] = "chain.sha256"
LOCK: Final[str] = ".lock"

_FILES: Final[frozenset[str]] = frozenset(
    STEP_LAYOUT[step]
    for step in (Step.PREPARE, Step.RUN, Step.VERDICT, Step.REVIEW)
)
_CAPTURE: Final[str] = STEP_LAYOUT[Step.CAPTURE] + "/"


@dataclass(frozen=True)
class _Sealed:
    """已封口证据包：目录、清单与链头三者绑定。"""

    pack: Path
    manifest: Manifest
    chain: str


class Store:
    """append-only 证据库；根目录下按 <日期>/<实验编号> 组织证据包。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def create(self, date: str, exp_id: str) -> Path:
        """新建证据包目录；目录已存在即拒绝，保证不回写历史。"""
        check_date(date)
        _check_id(exp_id)
        day = self.root / date
        day.mkdir(mode=0o755, parents=True, exist_ok=True)
        pack = day / exp_id
        pack.mkdir(mode=0o755)
        return pack

    def write(self, date: str, exp_id: str, relpath: str, data: bytes) -> str:
        """写入一份证据并返回 sha256；已存在或已封口均拒绝。"""
        pack = self._pack(date, exp_id)
        rel = _slot(relpath)
        if not pack.is_dir():
            raise FileNotFoundError(f"evidence pack not created: {pack}")
        with self._locked():
            if (pack / CHAIN).exists():
                raise FileExistsError(f"evidence pack is sealed: {pack}")
            target = pack / rel
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            _create(target, data)
        return _digest(target)

    def digest(self, date: str, exp_id: str, relpath: str) -> str:
        """复核已落盘证据的 sha256，供判定与复核步引用。"""
        return _digest(self._pack(date, exp_id) / _slot(relpath))

    def seal(self, man: Manifest) -> Path:
        """封口证据包：校验链头与摘要，写入清单并冻结目录。"""
        pack = self._pack(man.date, man.exp_id)
        with self._locked():
            _check_new(pack)
            packs = self._walk()
            head = packs[-1].chain if packs else ""
            if man.prev_chain_head != head:
                raise ValueError(
                    f"prev_chain_head must be {head!r}, "
                    f"got {man.prev_chain_head!r}"
                )
            self._check_super(man, packs)
            _check_tree(pack)
            self._check_files(pack, man.result.evidences)
            sealed = seal_manifest(man)
            _write_seal(pack, man, sealed)
        return pack / CHAIN

    def pack(self, date: str, exp_id: str) -> Path:
        """证据包目录路径；只做命名校验，不保证目录存在。"""
        return self._pack(date, exp_id)

    def find(self, exp_id: str) -> Manifest | None:
        """按实验编号查找已封口包；编号全局唯一，未封口包不可见。"""
        _check_id(exp_id)
        with self._locked():
            for item in self._walk():
                if item.manifest.exp_id == exp_id:
                    return item.manifest
        return None

    def load(self, date: str, exp_id: str) -> Manifest:
        """读取并复核已封口证据包；链摘要不符即拒绝。"""
        return _sealed(self._pack(date, exp_id)).manifest

    def head(self) -> str:
        """当前全项目链头；首包之前为空串。"""
        with self._locked():
            return self._head()

    def verify_all(self) -> int:
        """逐包复核链连续性与证据摘要，返回已封口包数量。"""
        with self._locked():
            packs = self._walk()
            for item in packs:
                self._check_files(item.pack, item.manifest.result.evidences)
            return len(packs)

    def _pack(self, date: str, exp_id: str) -> Path:
        """定位证据包目录，仅做命名校验，不保证存在。"""
        check_date(date)
        _check_id(exp_id)
        return self.root / date / exp_id

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """根目录排他锁，串行化封口与链头读取。"""
        with _root_lock(self.root):
            yield

    def _head(self) -> str:
        """链尾摘要；同时校验整条链未分叉。"""
        packs = self._walk()
        return packs[-1].chain if packs else ""

    def _walk(self) -> list[_Sealed]:
        """按 (日期, 实验编号) 顺序返回已封口包并校验前链头。"""
        packs = _collect(self.root)
        prev = ""
        for item in packs:
            if item.manifest.prev_chain_head != prev:
                raise ValueError(f"broken evidence chain at {item.pack}")
            prev = item.chain
        return packs

    def _check_super(self, man: Manifest, packs: list[_Sealed]) -> None:
        """实验编号全局唯一；supersedes 指向更早日期的已封口包。"""
        if any(item.manifest.exp_id == man.exp_id for item in packs):
            raise ValueError(f"duplicate experiment id: {man.exp_id}")
        if not man.supersedes:
            return
        target = [item for item in packs
                  if item.manifest.exp_id == man.supersedes]
        if not target:
            raise ValueError(f"supersedes unknown experiment: {man.supersedes}")
        if target[0].pack.parent.name >= man.date:
            raise ValueError("supersedes must start a new date directory")
        if any(item.manifest.supersedes == man.supersedes for item in packs):
            raise ValueError(f"{man.supersedes} is already superseded")

    def _check_files(self, pack: Path, evidences: list[Evidence]) -> None:
        """证据必须真实落盘且摘要一致，符号链接一律拒绝。"""
        for item in evidences:
            path = pack / item.path
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError(f"evidence missing: {item.path}")
            if _digest(path) != item.sha256:
                raise ValueError(f"evidence digest mismatch: {item.path}")


@contextmanager
def _root_lock(root: Path) -> Iterator[None]:
    """在证据根目录上加排他 flock；目录缺失时先建立。"""
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(root / LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _collect(root: Path) -> list[_Sealed]:
    """扫描根目录下所有已封口证据包，忽略未封口的在途目录。"""
    if not root.is_dir():
        return []
    packs: list[_Sealed] = []
    for day in sorted(item for item in root.iterdir() if item.is_dir()):
        check_date(day.name)
        for pack in sorted(item for item in day.iterdir() if item.is_dir()):
            _check_id(pack.name)
            if (pack / CHAIN).is_file():
                packs.append(_sealed(pack))
    return packs


def _sealed(pack: Path) -> _Sealed:
    """读取并校验单个已封口包，目录名必须与清单一致。"""
    man = load_manifest((pack / MANIFEST).read_text(encoding="utf-8"))
    if man.date != pack.parent.name or man.exp_id != pack.name:
        raise ValueError(f"manifest does not match directory: {pack}")
    chain = (pack / CHAIN).read_text(encoding="ascii").strip()
    if not verify(man, chain):
        raise ValueError(f"evidence chain mismatch: {pack}")
    return _Sealed(pack, man, chain)


def _check_new(pack: Path) -> None:
    """封口前确认包目录存在且尚未写入清单或链文件。"""
    if not pack.is_dir():
        raise FileNotFoundError(f"evidence pack not created: {pack}")
    if (pack / MANIFEST).exists() or (pack / CHAIN).exists():
        raise FileExistsError(f"evidence pack already sealed: {pack}")


def _check_id(exp_id: str) -> None:
    """实验编号必须路径安全，避免目录逃逸。"""
    if not EXP_ID_RE.fullmatch(exp_id):
        raise ValueError(f"invalid exp_id: {exp_id!r}")


def _check_tree(pack: Path) -> None:
    """封口前确认包内只有普通文件与目录，链接与特殊文件一律拒绝。"""
    for path in pack.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed in evidence: {path}")
        if not path.is_file() and not path.is_dir():
            raise ValueError(f"special file is not allowed in evidence: {path}")


def _write_seal(pack: Path, man: Manifest, sealed: str) -> None:
    """写入清单与链头后冻结；链头写失败即回滚清单，避免半封口。"""
    _create(pack / MANIFEST, dump(man).encode("utf-8"))
    try:
        _create(pack / CHAIN, (sealed + "\n").encode("ascii"))
        _freeze(pack)
    except BaseException:
        for name in (CHAIN, MANIFEST):
            (pack / name).unlink(missing_ok=True)
        raise


def _slot(relpath: str) -> str:
    """证据只能落在六步约定的文件或采集目录内。"""
    need_relpath(relpath)
    if relpath in _FILES or relpath.startswith(_CAPTURE):
        return relpath
    raise ValueError(f"not an evidence slot: {relpath!r}")


def _create(path: Path, data: bytes) -> None:
    """原子新建文件；目标已存在时抛出 FileExistsError。"""
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp, path)
        _sync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _sync_dir(path: Path) -> None:
    """把目录项变更刷入磁盘，保证封口后可跨崩溃恢复。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _digest(path: Path) -> str:
    """流式计算文件 sha256，避免大日志占用内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _freeze(pack: Path) -> None:
    """把证据包整体转为只读，阻断封口后的误写。"""
    for path in sorted(pack.rglob("*"), key=lambda item: len(item.parts),
                       reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(pack, 0o555)
