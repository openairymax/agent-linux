# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_store 单元测试：append-only 落盘、封口冻结与跨包链校验。"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import osaccrt_evidence as oe
import osaccrt_model as om
import osaccrt_store as os_

DATE = "2026-01-19"
NEXT = "2026-01-20"
DIGEST = "a" * 64
IMAGE = "b" * 64
RUN = "step-02-run.log"


def make_th() -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold("boot.time", "OS-ACC-BOOT-010", "60", "s")


def make_man(exp_id: str, digest: str, *, date: str = DATE,
             prev: str = "", supersedes: str = "") -> oe.Manifest:
    """构造携带运行证据的最小清单。"""
    result = om.ExpResult(exp_id, (make_th(),))
    result.evidences.append(om.Evidence(RUN, digest, om.Step.RUN))
    return oe.Manifest(date, "qemu-x86_64", IMAGE, result, prev, supersedes)


def seal_one(store: os_.Store, exp_id: str = "exp-001", *,
             date: str = DATE) -> tuple[oe.Manifest, str]:
    """建包、落盘运行证据并封口，返回清单与链头。"""
    store.create(date, exp_id)
    digest = store.write(date, exp_id, RUN, b"ok\n")
    man = make_man(exp_id, digest, date=date)
    return man, store.seal(man).read_text(encoding="ascii").strip()


def pack_of(store: os_.Store, date: str = DATE,
            exp_id: str = "exp-001") -> Path:
    """证据包目录。"""
    return store.root / date / exp_id


def test_create_returns_pack(tmp_path: Path) -> None:
    """建包返回 <日期>/<实验编号> 目录。"""
    store = os_.Store(tmp_path / "evidence")
    assert store.create(DATE, "exp-001") == pack_of(store)


def test_create_rejects_duplicate(tmp_path: Path) -> None:
    """同一编号不得重复建包，历史目录只增不改。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    with pytest.raises(FileExistsError):
        store.create(DATE, "exp-001")


@pytest.mark.parametrize("date", ["2026-1-9", "2026-02-30", ""])
def test_create_rejects_bad_date(tmp_path: Path, date: str) -> None:
    """日期必须是零填充的合法日历日。"""
    with pytest.raises(ValueError, match="date"):
        os_.Store(tmp_path / "e").create(date, "exp-001")


@pytest.mark.parametrize("exp_id", ["", "../x", "a/b", "-x"])
def test_create_rejects_bad_id(tmp_path: Path, exp_id: str) -> None:
    """实验编号必须路径安全，避免目录逃逸。"""
    with pytest.raises(ValueError, match="exp_id"):
        os_.Store(tmp_path / "e").create(DATE, exp_id)


def test_write_returns_digest(tmp_path: Path) -> None:
    """落盘返回的摘要与实际文件一致。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    digest = store.write(DATE, "exp-001", RUN, b"ok\n")
    assert digest == store.digest(DATE, "exp-001", RUN)
    assert len(digest) == 64


def test_write_rejects_repeat(tmp_path: Path) -> None:
    """同一路径不得二次写入。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    store.write(DATE, "exp-001", RUN, b"ok\n")
    with pytest.raises(FileExistsError):
        store.write(DATE, "exp-001", RUN, b"second\n")


def test_write_requires_created_pack(tmp_path: Path) -> None:
    """写入不得隐式建包，必须先显式 create。"""
    store = os_.Store(tmp_path / "evidence")
    with pytest.raises(FileNotFoundError):
        store.write(DATE, "exp-001", RUN, b"ok\n")


@pytest.mark.parametrize(
    "relpath",
    ["manifest.json", "step-03-artifacts", "step-01-prepare.json/x", "../run.log"],
)
def test_write_rejects_bad_slot(tmp_path: Path, relpath: str) -> None:
    """证据只能落在六步约定的文件或采集目录内。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    with pytest.raises(ValueError):
        store.write(DATE, "exp-001", relpath, b"x")


def test_write_accepts_capture_child(tmp_path: Path) -> None:
    """采集产物可写入 step-03-artifacts 子路径。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    store.write(DATE, "exp-001", "step-03-artifacts/report.json", b"{}\n")
    assert (pack_of(store) / "step-03-artifacts" / "report.json").is_file()


def test_seal_writes_manifest_and_chain(tmp_path: Path) -> None:
    """封口写入清单与链头，且可原样读回。"""
    store = os_.Store(tmp_path / "evidence")
    man, chain = seal_one(store)
    pack = pack_of(store)
    assert (pack / "manifest.json").is_file()
    assert (pack / "chain.sha256").read_text(encoding="ascii").strip() == chain
    assert store.load(DATE, "exp-001") == man
    assert store.head() == chain


def test_seal_rejects_missing_pack(tmp_path: Path) -> None:
    """未建包不得封口。"""
    store = os_.Store(tmp_path / "evidence")
    with pytest.raises(FileNotFoundError):
        store.seal(make_man("exp-001", DIGEST))


def test_seal_rejects_empty_pack(tmp_path: Path) -> None:
    """空证据包拒绝封口。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    man = oe.Manifest(DATE, "qemu-x86_64", IMAGE,
                      om.ExpResult("exp-001", (make_th(),)))
    with pytest.raises(ValueError, match="empty"):
        store.seal(man)


def test_seal_rejects_missing_evidence(tmp_path: Path) -> None:
    """清单登记的证据必须真实落盘。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    with pytest.raises(FileNotFoundError):
        store.seal(make_man("exp-001", DIGEST))


def test_seal_rejects_digest_mismatch(tmp_path: Path) -> None:
    """证据摘要与清单不符即拒绝封口。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    store.write(DATE, "exp-001", RUN, b"ok\n")
    with pytest.raises(ValueError, match="mismatch"):
        store.seal(make_man("exp-001", DIGEST))


def test_seal_rejects_wrong_prev(tmp_path: Path) -> None:
    """前链头必须是当前链头，避免链分叉。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    digest = store.write(DATE, "exp-001", RUN, b"ok\n")
    with pytest.raises(ValueError, match="prev_chain_head"):
        store.seal(make_man("exp-001", digest, prev=DIGEST))


def test_seal_rejects_second_call(tmp_path: Path) -> None:
    """已封口包不得再次封口。"""
    store = os_.Store(tmp_path / "evidence")
    man, _ = seal_one(store)
    with pytest.raises(FileExistsError):
        store.seal(man)


def test_seal_rejects_symlink(tmp_path: Path) -> None:
    """包内出现符号链接即拒绝封口，避免取证明偷换目标。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    digest = store.write(DATE, "exp-001", RUN, b"ok\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (pack_of(store) / "extra").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        store.seal(make_man("exp-001", digest))


def test_seal_rejects_symlink_evidence(tmp_path: Path) -> None:
    """指向包外的证据链接一律拒绝。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    artifacts = pack_of(store) / "step-03-artifacts"
    artifacts.mkdir()
    (artifacts / "report.json").symlink_to(outside)
    item = om.Evidence("step-03-artifacts/report.json", DIGEST,
                       om.Step.CAPTURE)
    result = om.ExpResult("exp-001", (make_th(),), [item])
    with pytest.raises(ValueError, match="symlink"):
        store.seal(oe.Manifest(DATE, "qemu-x86_64", IMAGE, result))


def test_freeze_sets_readonly(tmp_path: Path) -> None:
    """封口后目录与文件转为只读。"""
    store = os_.Store(tmp_path / "evidence")
    seal_one(store)
    pack = pack_of(store)
    assert stat.S_IMODE(pack.stat().st_mode) == 0o555
    assert stat.S_IMODE((pack / "manifest.json").stat().st_mode) == 0o444
    assert stat.S_IMODE((pack / RUN).stat().st_mode) == 0o444


def test_write_after_seal_rejected(tmp_path: Path) -> None:
    """封口后不得再写入证据。"""
    store = os_.Store(tmp_path / "evidence")
    seal_one(store)
    with pytest.raises(FileExistsError):
        store.write(DATE, "exp-001", "step-01-prepare.json", b"{}")


def test_chain_continues(tmp_path: Path) -> None:
    """第二个包以前链头接续，链头推进且两包可复核。"""
    store = os_.Store(tmp_path / "evidence")
    _, first = seal_one(store, "exp-001")
    store.create(DATE, "exp-002")
    digest = store.write(DATE, "exp-002", RUN, b"ok\n")
    man = make_man("exp-002", digest, prev=first)
    second = store.seal(man).read_text(encoding="ascii").strip()
    assert second != first
    assert store.head() == second
    assert store.verify_all() == 2


def test_seal_rejects_duplicate_exp_id(tmp_path: Path) -> None:
    """实验编号全局唯一，跨日期也不得重复。"""
    store = os_.Store(tmp_path / "evidence")
    _, first = seal_one(store, "exp-001")
    store.create(NEXT, "exp-001")
    digest = store.write(NEXT, "exp-001", RUN, b"ok\n")
    with pytest.raises(ValueError, match="duplicate"):
        store.seal(make_man("exp-001", digest, date=NEXT, prev=first))


def test_seal_accepts_new_date_supersede(tmp_path: Path) -> None:
    """取代实验必须落在更晚的日期目录。"""
    store = os_.Store(tmp_path / "evidence")
    _, first = seal_one(store, "exp-001")
    store.create(NEXT, "exp-002")
    digest = store.write(NEXT, "exp-002", RUN, b"ok\n")
    man = make_man("exp-002", digest, date=NEXT, prev=first,
                   supersedes="exp-001")
    store.seal(man)
    assert store.load(NEXT, "exp-002").supersedes == "exp-001"


def test_seal_rejects_unknown_supersede(tmp_path: Path) -> None:
    """supersedes 必须指向已封口的实验。"""
    store = os_.Store(tmp_path / "evidence")
    _, first = seal_one(store, "exp-001")
    store.create(NEXT, "exp-002")
    digest = store.write(NEXT, "exp-002", RUN, b"ok\n")
    man = make_man("exp-002", digest, date=NEXT, prev=first,
                   supersedes="exp-999")
    with pytest.raises(ValueError, match="unknown"):
        store.seal(man)


def test_seal_rejects_same_date_supersede(tmp_path: Path) -> None:
    """同日目录内不得取代，必须新开日期目录。"""
    store = os_.Store(tmp_path / "evidence")
    _, first = seal_one(store, "exp-001")
    store.create(DATE, "exp-002")
    digest = store.write(DATE, "exp-002", RUN, b"ok\n")
    man = make_man("exp-002", digest, prev=first, supersedes="exp-001")
    with pytest.raises(ValueError, match="new date"):
        store.seal(man)


def test_seal_rejects_double_supersede(tmp_path: Path) -> None:
    """同一实验只能被取代一次，避免结论分支。"""
    store = os_.Store(tmp_path / "evidence")
    _, first = seal_one(store, "exp-001")
    store.create(NEXT, "exp-002")
    digest = store.write(NEXT, "exp-002", RUN, b"ok\n")
    man = make_man("exp-002", digest, date=NEXT, prev=first,
                   supersedes="exp-001")
    second = store.seal(man).read_text(encoding="ascii").strip()
    store.create("2026-01-21", "exp-003")
    digest = store.write("2026-01-21", "exp-003", RUN, b"ok\n")
    man = make_man("exp-003", digest, date="2026-01-21", prev=second,
                   supersedes="exp-001")
    with pytest.raises(ValueError, match="already superseded"):
        store.seal(man)


def test_verify_all_detects_tampered_evidence(tmp_path: Path) -> None:
    """封口后改写证据内容会被摘要复核发现。"""
    store = os_.Store(tmp_path / "evidence")
    seal_one(store)
    path = pack_of(store) / RUN
    os.chmod(path, 0o644)
    path.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        store.verify_all()


def test_load_detects_tampered_chain(tmp_path: Path) -> None:
    """改写链头摘要会被封口复核发现。"""
    store = os_.Store(tmp_path / "evidence")
    seal_one(store)
    chain = pack_of(store) / "chain.sha256"
    os.chmod(chain, 0o644)
    chain.write_text(DIGEST + "\n", encoding="ascii")
    with pytest.raises(ValueError, match="chain mismatch"):
        store.load(DATE, "exp-001")


def test_load_detects_tampered_manifest(tmp_path: Path) -> None:
    """改写清单元数据会改变链头，复核即失败。"""
    store = os_.Store(tmp_path / "evidence")
    seal_one(store)
    path = pack_of(store) / "manifest.json"
    os.chmod(path, 0o644)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["host"] = "other-host"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="chain mismatch"):
        store.load(DATE, "exp-001")


def test_head_empty_before_seal(tmp_path: Path) -> None:
    """首包封口前链头为空。"""
    store = os_.Store(tmp_path / "evidence")
    assert store.head() == ""
    store.create(DATE, "exp-001")
    assert store.head() == ""


def test_verify_all_ignores_open_pack(tmp_path: Path) -> None:
    """未封口的在途包不参与链，也不阻断复核。"""
    store = os_.Store(tmp_path / "evidence")
    seal_one(store, "exp-001")
    store.create(NEXT, "exp-002")
    store.write(NEXT, "exp-002", RUN, b"ok\n")
    assert store.verify_all() == 1
