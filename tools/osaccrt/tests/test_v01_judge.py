# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""OS-ACC-RT 全链验收测试：六步规程串起四态判定。

本测试只走真实组件——`Store` 落盘、`Runner` 主循环、`PackJudge` 裁决、
`waiver` / `blocked` 复核与 `repro` 复现脚本——不替换任何环节，用以
证明「准备→执行→取证→判定→复核→归档」在 PASS、FAIL、WAIVED 与
BLOCKED 四条路径上都形成可追溯、可复算的证据包。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import osaccrt_blocked as ob
import osaccrt_judge as oj
import osaccrt_model as om
import osaccrt_repro as orep
import osaccrt_runner as orun
import osaccrt_store as os_
import osaccrt_waiver as ow

DATE = "2026-08-13"
IMAGE = "c" * 64
HOST = "qemu-x86_64"
PREP = "step-01-prepare.json"
RUN = "step-02-run.log"
CAP = "step-03-artifacts"
VERDICT = "step-04-verdict.json"
REVIEW = "step-05-review.json"
SIG = ("a@example.com", "b@example.com")


def make_th(key: str = "boot.time") -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold(key, "OS-ACC-BOOT-010", "60", "s")


def make_plan(exp_id: str = "exp-001",
              keys: tuple[str, ...] = ("boot.time",),
              produced: tuple[str, ...] | None = None) -> orun.ExpPlan:
    """构造真实三步计划；produced 限定采集产物以制造判定缺口。"""
    outs = keys if produced is None else produced

    def prepare(_pack: Path) -> dict[str, bytes]:
        return {PREP: b"{}\n"}

    def run(_pack: Path) -> dict[str, bytes]:
        return {RUN: b"run ok\n"}

    def capture(_pack: Path) -> dict[str, bytes]:
        return {f"{CAP}/{key}": b"1\n" for key in outs}

    return orun.ExpPlan(
        exp_id,
        tuple(make_th(key) for key in keys),
        (
            (om.Step.PREPARE, prepare),
            (om.Step.RUN, run),
            (om.Step.CAPTURE, capture),
        ),
    )


def make_runner(root: Path) -> orun.Runner:
    """固定日期与镜像摘要的执行器，便于断言链头。"""
    return orun.Runner(
        os_.Store(root), oj.PackJudge(), HOST, IMAGE, DATE
    )


def make_waiver(basis: ow.Basis = ow.Basis.ACCEPTED,
                evidence: str = "") -> ow.Waiver:
    """构造一份双签豁免，用于复核步。"""
    return ow.Waiver(basis, "仅影响日志格式", evidence, SIG)


def test_pass_pack_is_replayable(tmp_path: Path) -> None:
    """PASS 链：判定、封口与复现脚本三处一致，首包前链头为空。"""
    root = tmp_path / "evidence"
    out = make_runner(root).run(make_plan())
    man = os_.Store(root).load(DATE, "exp-001")
    script = orep.render(man, "qemu-system-x86_64 -m 4G")
    assert out.verdict is om.Verdict.PASS
    assert man.result.verdict is om.Verdict.PASS
    assert man.prev_chain_head == ""
    assert "# verdict: PASS" in script
    assert man.chain_head in script
    assert "qemu-system-x86_64 -m 4G" in script


def test_fail_then_pass_links_chain(tmp_path: Path) -> None:
    """FAIL 与 PASS 按封口顺序串成单链，前一链头写入后一清单。"""
    root = tmp_path / "evidence"
    store = os_.Store(root)
    first, second = make_runner(root).run_all((
        make_plan(keys=("boot.time", "mem.peak"), produced=("boot.time",)),
        make_plan("exp-002"),
    ))
    man = store.load(DATE, "exp-002")
    assert first.verdict is om.Verdict.FAIL
    assert second.verdict is om.Verdict.PASS
    assert man.prev_chain_head == first.chain
    assert store.verify_all() == 2


def test_waived_needs_double_sign(tmp_path: Path) -> None:
    """FAIL 经双签复核转为 WAIVED，复核记录随包封口并可反查。"""
    root = tmp_path / "evidence"
    item = make_waiver(ow.Basis.EQUIVALENT, f"{CAP}/boot.time")

    def review(result: om.ExpResult) -> orun.Ruling:
        return orun.Ruling(ow.apply(result, item), {REVIEW: ow.record(item)})

    plan = make_plan(keys=("boot.time", "mem.peak"), produced=("boot.time",))
    out = make_runner(root).run(plan, review)
    store = os_.Store(root)
    man = store.load(DATE, "exp-001")
    assert out.verdict is om.Verdict.WAIVED
    assert man.result.waiver_sig == SIG
    assert ow.verify(man, item) is True
    assert (store.pack(DATE, "exp-001") / REVIEW).is_file()
    script = orep.render(man, "true")
    assert "# waiver-sig: a@example.com, b@example.com" in script


def test_blocked_stays_at_prepare(tmp_path: Path) -> None:
    """实机缺失时只落准备证据，复核给出 BLOCKED 且不入通过率。"""
    root = tmp_path / "evidence"
    entry = ob.lookup("sw_64")
    plan = orun.ExpPlan(
        entry.exps[0],
        (make_th(),),
        ((om.Step.PREPARE, lambda _pack: {PREP: ob.record(entry)}),),
    )

    def review(result: om.ExpResult) -> orun.Ruling:
        return orun.Ruling(ob.apply(result, entry.key))

    out = make_runner(root).run(plan, review)
    store = os_.Store(root)
    man = store.load(DATE, entry.exps[0])
    assert out.verdict is om.Verdict.BLOCKED
    assert ob.verify(man) is True
    assert {item.path for item in man.result.evidences} == {PREP}
    assert not (store.pack(DATE, entry.exps[0]) / VERDICT).exists()
    assert ob.ledger([man.result]) == ((entry.exps[0], entry.key),)


def test_review_record_mismatch_rejected(tmp_path: Path) -> None:
    """续跑时复核记录与本次复核不一致即拒绝，杜绝事后改签。"""
    root = tmp_path / "evidence"
    store = os_.Store(root)
    store.create(DATE, "exp-001")
    store.write(DATE, "exp-001", PREP, b"{}\n")
    store.write(DATE, "exp-001", RUN, b"run ok\n")
    store.write(DATE, "exp-001", f"{CAP}/boot.time", b"1\n")
    store.write(DATE, "exp-001", VERDICT, _failed())
    store.write(DATE, "exp-001", REVIEW, b'{"basis": "accepted"}\n')
    item = make_waiver()

    def review(result: om.ExpResult) -> orun.Ruling:
        return orun.Ruling(ow.apply(result, item), {REVIEW: ow.record(item)})

    with pytest.raises(ValueError, match="review record mismatch"):
        make_runner(root).run(make_plan(), review)


def _failed() -> bytes:
    """模拟上次中断前落盘的 FAIL 判定记录。"""
    data = {
        "exp_id": "exp-001",
        "keys": ["boot.time"],
        "missing": [],
        "verdict": "FAIL",
    }
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return (text + "\n").encode("utf-8")
