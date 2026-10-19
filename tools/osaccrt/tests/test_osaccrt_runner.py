# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_runner 单元测试：步骤主循环、判定落盘与断点续跑。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import osaccrt_evidence as oe
import osaccrt_judge as oj
import osaccrt_model as om
import osaccrt_runner as orun
import osaccrt_store as os_

DATE = "2026-01-19"
IMAGE = "b" * 64
HOST = "qemu-x86_64"
PREP = "step-01-prepare.json"
RUN = "step-02-run.log"
CAP = "step-03-artifacts"
VERDICT = "step-04-verdict.json"


def make_th(key: str = "boot.time") -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold(key, "OS-ACC-BOOT-010", "60", "s")


def make_plan(exp_id: str = "exp-001", calls: list[om.Step] | None = None,
              keys: tuple[str, ...] = ("boot.time",),
              produced: tuple[str, ...] | None = None) -> orun.ExpPlan:
    """构造三步计划；calls 记录调用顺序，produced 限定采集产物。"""
    log: list[om.Step] = [] if calls is None else calls
    outs = keys if produced is None else produced

    def prepare(_pack: Path) -> dict[str, bytes]:
        log.append(om.Step.PREPARE)
        return {PREP: b"{}\n"}

    def run(_pack: Path) -> dict[str, bytes]:
        log.append(om.Step.RUN)
        return {RUN: b"run ok\n"}

    def capture(_pack: Path) -> dict[str, bytes]:
        log.append(om.Step.CAPTURE)
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


def make_store(tmp_path: Path) -> os_.Store:
    """隔离在临时目录下的证据库。"""
    return os_.Store(tmp_path / "evidence")


def make_runner(store: os_.Store, host: str = HOST) -> orun.Runner:
    """固定日期与镜像摘要的执行器，便于断言链头。"""
    return orun.Runner(store, oj.PackJudge(), host, IMAGE, DATE)


def seed(store: os_.Store, exp_id: str = "exp-001",
         keys: tuple[str, ...] = ("boot.time",)) -> Path:
    """预置未封口包：准备、执行与完整采集产物。"""
    store.create(DATE, exp_id)
    store.write(DATE, exp_id, PREP, b"{}\n")
    store.write(DATE, exp_id, RUN, b"run ok\n")
    for key in keys:
        store.write(DATE, exp_id, f"{CAP}/{key}", b"1\n")
    return store.pack(DATE, exp_id)


def test_run_passes_and_seals(tmp_path: Path) -> None:
    """三步齐全且阈值无缺口时判定 PASS 并封口。"""
    store = make_store(tmp_path)
    calls: list[om.Step] = []
    out = make_runner(store).run(make_plan(calls=calls))
    assert out.verdict is om.Verdict.PASS
    assert out.resumed is False
    assert calls == [om.Step.PREPARE, om.Step.RUN, om.Step.CAPTURE]
    assert store.verify_all() == 1
    assert store.load(DATE, "exp-001").result.verdict is om.Verdict.PASS


def test_run_fails_on_missing_artifact(tmp_path: Path) -> None:
    """阈值缺取证产物时判定 FAIL，但证据仍完整封口。"""
    store = make_store(tmp_path)
    plan = make_plan(keys=("boot.time", "mem.peak"),
                     produced=("boot.time",))
    out = make_runner(store).run(plan)
    assert out.verdict is om.Verdict.FAIL
    payload = (store.pack(DATE, "exp-001") / VERDICT).read_text(
        encoding="utf-8")
    assert '"mem.peak"' in payload


def test_resume_skips_finished_steps(tmp_path: Path) -> None:
    """上次中断后只执行缺口步骤，已落盘证据不重复取证。"""
    store = make_store(tmp_path)
    broken = orun.ExpPlan(
        "exp-001",
        (make_th(),),
        (
            (om.Step.PREPARE, lambda _pack: {PREP: b"{}\n"}),
            (om.Step.RUN, _explode),
            (om.Step.CAPTURE, lambda _pack: {f"{CAP}/boot.time": b"1\n"}),
        ),
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        make_runner(store).run(broken)
    calls: list[om.Step] = []
    out = make_runner(store).run(make_plan(calls=calls))
    assert out.resumed is True
    assert out.verdict is om.Verdict.PASS
    assert calls == [om.Step.RUN, om.Step.CAPTURE]


def test_partial_capture_rejected(tmp_path: Path) -> None:
    """采集产物不完整时拒绝续跑，避免两次运行数据混入。"""
    store = make_store(tmp_path)
    seed(store, keys=("boot.time",))
    calls: list[om.Step] = []
    plan = make_plan(keys=("boot.time", "mem.peak"), calls=calls)
    with pytest.raises(ValueError, match="partial capture"):
        make_runner(store).run(plan)
    assert calls == []


def test_reuse_sealed_pack(tmp_path: Path) -> None:
    """已封口包直接复用结论，不再次执行任何步骤。"""
    store = make_store(tmp_path)
    first = make_runner(store).run(make_plan())
    calls: list[om.Step] = []
    again = make_runner(store).run(make_plan(calls=calls))
    assert calls == []
    assert again.resumed is True
    assert again.chain == first.chain
    assert again.verdict is om.Verdict.PASS


def test_reuse_rejects_other_thresholds(tmp_path: Path) -> None:
    """同一编号的阈值表必须一致，否则拒绝复用。"""
    store = make_store(tmp_path)
    make_runner(store).run(make_plan())
    with pytest.raises(ValueError, match="threshold table"):
        make_runner(store).run(make_plan(keys=("mem.peak",)))


def test_reuse_rejects_other_host(tmp_path: Path) -> None:
    """证据包只能在同一宿主与同一镜像上复用。"""
    store = make_store(tmp_path)
    make_runner(store).run(make_plan())
    with pytest.raises(ValueError, match="produced elsewhere"):
        make_runner(store, host="qemu-aarch64").run(make_plan())


def test_reuse_rejects_failed_pack(tmp_path: Path) -> None:
    """封口包缺少判定时不得复用。"""
    store = make_store(tmp_path)
    seed(store)
    result = om.ExpResult("exp-001", (make_th(),))
    for rel, step in (
        (PREP, om.Step.PREPARE),
        (RUN, om.Step.RUN),
        (f"{CAP}/boot.time", om.Step.CAPTURE),
    ):
        result.evidences.append(om.Evidence(
            rel, store.digest(DATE, "exp-001", rel), step))
    store.seal(oe.Manifest(DATE, HOST, IMAGE, result))
    with pytest.raises(ValueError, match="no verdict"):
        make_runner(store).run(make_plan())


def test_recorded_verdict_resumed(tmp_path: Path) -> None:
    """中断前已落盘的判定原样接续，不重新裁决。"""
    store = make_store(tmp_path)
    pack = seed(store)
    payload = (
        '{"exp_id": "exp-001", "keys": ["boot.time"], '
        '"missing": [], "verdict": "FAIL"}\n'
    )
    store.write(DATE, "exp-001", VERDICT, payload.encode("utf-8"))
    calls: list[om.Step] = []
    out = make_runner(store).run(make_plan(calls=calls))
    assert calls == []
    assert out.verdict is om.Verdict.FAIL
    assert store.verify_all() == 1
    assert pack.is_dir()


def test_recorded_verdict_bad_json(tmp_path: Path) -> None:
    """判定文件语法错误时拒绝续跑，不猜测上次结论。"""
    store = make_store(tmp_path)
    seed(store)
    store.write(DATE, "exp-001", VERDICT, b"{")
    with pytest.raises(ValueError, match="not valid JSON"):
        make_runner(store).run(make_plan())


def test_capture_symlink_rejected(tmp_path: Path) -> None:
    """采集目录内的符号链接一律拒绝，防止证据逃逸。"""
    store = make_store(tmp_path)
    pack = seed(store)
    target = pack / CAP / "boot.time"
    target.unlink()
    os.symlink(pack / RUN, target)
    with pytest.raises(ValueError, match="symlink"):
        make_runner(store).run(make_plan())


def test_prepare_wrong_slot_rejected(tmp_path: Path) -> None:
    """准备步只能写准备证据，不得占用其他步骤位置。"""
    plan = orun.ExpPlan(
        "exp-001",
        (make_th(),),
        (
            (om.Step.PREPARE, lambda _pack: {RUN: b"x"}),
            (om.Step.RUN, lambda _pack: {RUN: b"x"}),
            (om.Step.CAPTURE, lambda _pack: {f"{CAP}/boot.time": b"1\n"}),
        ),
    )
    with pytest.raises(ValueError, match="prepare evidence"):
        make_runner(make_store(tmp_path)).run(plan)


def test_capture_outside_root_rejected(tmp_path: Path) -> None:
    """采集产物必须落在 step-03-artifacts 目录内。"""
    plan = orun.ExpPlan(
        "exp-001",
        (make_th(),),
        (
            (om.Step.PREPARE, lambda _pack: {PREP: b"{}\n"}),
            (om.Step.RUN, lambda _pack: {RUN: b"x"}),
            (om.Step.CAPTURE, lambda _pack: {"stray/boot.time": b"1\n"}),
        ),
    )
    with pytest.raises(ValueError, match="must live under"):
        make_runner(make_store(tmp_path)).run(plan)


def test_empty_step_rejected(tmp_path: Path) -> None:
    """步骤函数必须产出证据，空映射即为无效实现。"""
    plan = orun.ExpPlan(
        "exp-001",
        (make_th(),),
        (
            (om.Step.PREPARE, lambda _pack: {PREP: b"{}\n"}),
            (om.Step.RUN, lambda _pack: {RUN: b"x"}),
            (om.Step.CAPTURE, lambda _pack: {}),
        ),
    )
    with pytest.raises(ValueError, match="produced no evidence"):
        make_runner(make_store(tmp_path)).run(plan)


def test_pack_path_must_be_directory(tmp_path: Path) -> None:
    """同名路径是普通文件时拒绝执行，避免误写。"""
    store = make_store(tmp_path)
    pack = store.pack(DATE, "exp-001")
    pack.parent.mkdir(parents=True)
    pack.write_text("stray\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        make_runner(store).run(make_plan())


def test_plan_requires_three_steps() -> None:
    """计划必须恰为准备、执行、取证三步且顺序固定。"""
    noop = lambda _pack: {PREP: b"{}\n"}
    with pytest.raises(ValueError, match="in order"):
        orun.ExpPlan("exp-001", (make_th(),),
                     ((om.Step.PREPARE, noop), (om.Step.RUN, noop)))
    with pytest.raises(ValueError, match="in order"):
        orun.ExpPlan(
            "exp-001",
            (make_th(),),
            (
                (om.Step.RUN, noop),
                (om.Step.PREPARE, noop),
                (om.Step.CAPTURE, noop),
            ),
        )


def test_run_rejects_non_plan(tmp_path: Path) -> None:
    """执行器只接受 ExpPlan，拒绝鸭子类型。"""
    runner = make_runner(make_store(tmp_path))
    with pytest.raises(TypeError, match="ExpPlan"):
        runner.run(object())  # type: ignore[arg-type]


def test_run_all_rejects_duplicate_ids(tmp_path: Path) -> None:
    """一组计划内编号重复时在取证前拒绝。"""
    store = make_store(tmp_path)
    plan = make_plan()
    with pytest.raises(ValueError, match="duplicate exp_id"):
        make_runner(store).run_all((plan, plan))
    assert not store.pack(DATE, "exp-001").exists()


def test_runner_validates_inputs(tmp_path: Path) -> None:
    """宿主、镜像摘要与日期在构造时校验。"""
    store = make_store(tmp_path)
    judge = oj.PackJudge()
    with pytest.raises(ValueError, match="host"):
        orun.Runner(store, judge, "", IMAGE, DATE)
    with pytest.raises(ValueError, match="image_sha256"):
        orun.Runner(store, judge, HOST, "xyz", DATE)
    with pytest.raises(ValueError, match="date"):
        orun.Runner(store, judge, HOST, IMAGE, "2026-1-9")


def _explode(_pack: Path) -> dict[str, bytes]:
    """模拟执行步中途崩溃，留下未封口证据包。"""
    raise RuntimeError("interrupted")
