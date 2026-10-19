# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_blocked 单元测试：预登记范围、准备步入口与台账。"""

from __future__ import annotations

import json
from typing import Any

import pytest

import osaccrt_blocked as ob
import osaccrt_evidence as oe
import osaccrt_judge as oj
import osaccrt_model as om
import osaccrt_store as os_

DATE = "2026-01-20"
HOST = "qemu-x86_64"
IMAGE = "b" * 64
PREP = "step-01-prepare.json"
RUN = "step-02-run.log"
EXPECTED = {
    "sw_64": ("sw_64 裸机", ("E1-02", "E1-04", "E1-05")),
    "loongarch": ("loongarch 裸机", ("E1-02", "E1-04")),
    "m2-smp": ("M2 SMP 大规模多核实机", ("E3-03", "E3-04")),
}
FALLBACK = {
    "sw_64": "改用 QEMU 等效镜像冒烟",
    "loongarch": "改用 QEMU 等效镜像冒烟",
    "m2-smp": "改用中小规模 QEMU SMP 实测并附外推说明",
}


def make_th() -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold("boot.time", "OS-ACC-BOOT-010", "60", "s")


def prepared(exp_id: str = "E1-02") -> om.ExpResult:
    """构造只含准备证据、尚未判定的结果。"""
    result = om.ExpResult(exp_id, (make_th(),))
    result.evidences.append(
        om.Evidence(PREP, "a" * 64, om.Step.PREPARE))
    return result


def ran(exp_id: str = "E1-02") -> om.ExpResult:
    """构造已进入执行步的结果，用于证明入口受限。"""
    result = prepared(exp_id)
    result.evidences.append(om.Evidence(RUN, "a" * 64, om.Step.RUN))
    return result


def record_of(key: str = "sw_64") -> dict[str, Any]:
    """把登记记录解析为字典，供篡改字段后回灌。"""
    return json.loads(ob.record(ob.lookup(key)))


def blocked(exp_id: str = "E1-02",
            steps: tuple[om.Step, ...] = (om.Step.PREPARE,),
            reason: str = "") -> om.ExpResult:
    """构造未落盘的 BLOCKED 结果，用于互证边界。"""
    result = om.ExpResult(exp_id, (make_th(),))
    for step in steps:
        path = PREP if step is om.Step.PREPARE else RUN
        result.evidences.append(om.Evidence(path, "a" * 64, step))
    result.verdict = om.Verdict.BLOCKED
    result.blocked_reason = reason or ob.lookup("sw_64").reason
    return result


def seal(store: os_.Store, key: str = "sw_64",
         exp_id: str = "E1-02") -> oe.Manifest:
    """在给定仓储中真实落盘并封口一个降级包。"""
    store.create(DATE, exp_id)
    raw = ob.record(ob.lookup(key))
    digest = store.write(DATE, exp_id, PREP, raw)
    result = om.ExpResult(exp_id, (make_th(),))
    result.evidences.append(om.Evidence(PREP, digest, om.Step.PREPARE))
    store.seal(oe.Manifest(DATE, HOST, IMAGE, ob.apply(result, key)))
    return store.load(DATE, exp_id)


def test_registry_matches_task_card() -> None:
    """登记表与任务卡 §3.3 三项逐条一致。"""
    assert [item.key for item in ob.REGISTRY] == list(EXPECTED)
    for item in ob.REGISTRY:
        platform, exps = EXPECTED[item.key]
        assert item.platform == platform
        assert item.exps == exps
        assert item.fallback == FALLBACK[item.key]
        assert item.reason == f"{platform}缺失：{item.fallback}"


def test_registry_reason_text() -> None:
    """降级原因写明缺失机型与允许的降级方式，保持单行。"""
    reason = ob.lookup("sw_64").reason
    assert reason == "sw_64 裸机缺失：改用 QEMU 等效镜像冒烟"
    assert "\n" not in reason


def test_entry_shape_rules() -> None:
    """登记项拒绝非法键、空文本与多行文本。"""
    with pytest.raises(ValueError, match="blocked key"):
        ob.Entry("SW_64", "平台", ("E1-02",), "方式")
    with pytest.raises(ValueError, match="blocked key"):
        ob.Entry("", "平台", ("E1-02",), "方式")
    with pytest.raises(ValueError, match="platform"):
        ob.Entry("sw_64", "", ("E1-02",), "方式")
    with pytest.raises(ValueError, match="single visible line"):
        ob.Entry("sw_64", "两行\n平台", ("E1-02",), "方式")
    with pytest.raises(ValueError, match="fallback"):
        ob.Entry("sw_64", "平台", ("E1-02",), "")


def test_entry_rejects_bad_exps() -> None:
    """受影响实验必须为合法、去重的编号序列。"""
    with pytest.raises(ValueError, match="non-empty sequence"):
        ob.Entry("sw_64", "平台", "E1-02", "方式")
    with pytest.raises(ValueError, match="not be empty"):
        ob.Entry("sw_64", "平台", (), "方式")
    with pytest.raises(ValueError, match="invalid exp id"):
        ob.Entry("sw_64", "平台", ("-bad",), "方式")
    with pytest.raises(ValueError, match="invalid exp id"):
        ob.Entry("sw_64", "平台", (1,), "方式")
    with pytest.raises(ValueError, match="duplicate"):
        ob.Entry("sw_64", "平台", ("E1-02", "E1-02"), "方式")


def test_entry_normalizes_exps() -> None:
    """列表形式的实验表归一化为元组。"""
    item = ob.Entry("sw_64", "平台", ["E1-02"], "方式")
    assert item.exps == ("E1-02",)
    assert item.reason == "平台缺失：方式"


def test_lookup_registered() -> None:
    """登记键可取出对应项。"""
    for key in EXPECTED:
        assert ob.lookup(key).key == key


def test_lookup_rejects_unknown() -> None:
    """未登记键与非法键一律拒绝。"""
    with pytest.raises(ValueError, match="unregistered"):
        ob.lookup("riscv")
    with pytest.raises(ValueError, match="blocked key"):
        ob.lookup("SW_64")
    with pytest.raises(ValueError, match="blocked key"):
        ob.lookup("")


def test_apply_marks_blocked() -> None:
    """准备步结果登记为 BLOCKED，原对象不被修改。"""
    result = prepared()
    item = ob.apply(result, "sw_64")
    assert item.verdict is om.Verdict.BLOCKED
    assert item.blocked_reason == ob.lookup("sw_64").reason
    assert item.exp_id == "E1-02"
    assert item.thresholds == result.thresholds
    assert item.evidences == result.evidences
    assert item is not result
    assert result.verdict is None
    assert result.blocked_reason == ""


def test_apply_requires_prepare_only() -> None:
    """已进入执行步的结果不得再登记为 BLOCKED。"""
    with pytest.raises(ValueError, match="only reachable"):
        ob.apply(ran(), "sw_64")


def test_apply_rejects_other_exp() -> None:
    """实验不在登记影响面内一律拒绝。"""
    with pytest.raises(ValueError, match="does not block"):
        ob.apply(prepared("E3-03"), "sw_64")


def test_apply_rejects_types() -> None:
    """输入必须是实验结果对象。"""
    with pytest.raises(TypeError, match="ExpResult"):
        ob.apply(object(), "sw_64")  # type: ignore[arg-type]


def test_record_layout() -> None:
    """准备记录为排序键、中文不转义、以换行结尾的 JSON。"""
    text = ob.record(ob.lookup("m2-smp")).decode("utf-8")
    assert text.endswith("\n")
    assert "\\u" not in text
    data = json.loads(text)
    assert list(data) == ["exps", "fallback", "key", "platform"]
    assert data["key"] == "m2-smp"
    assert data["exps"] == ["E3-03", "E3-04"]


def test_record_rejects_types() -> None:
    """序列化只接受登记项。"""
    with pytest.raises(TypeError, match="Entry"):
        ob.record(object())  # type: ignore[arg-type]


def test_load_roundtrip() -> None:
    """文本与字节两种输入都能还原同一登记项。"""
    for key in EXPECTED:
        item = ob.lookup(key)
        raw = ob.record(item)
        assert ob.load(raw) == item
        assert ob.load(raw.decode("utf-8")) == item


def test_load_rejects_tamper() -> None:
    """平台、实验表与降级方式任一被改写都拒绝。"""
    cases = (
        ("platform", "其他平台", "platform mismatch"),
        ("exps", ["E3-03"], "exps mismatch"),
        ("fallback", "改用模拟", "fallback mismatch"),
    )
    for field, value, pattern in cases:
        data = record_of()
        data[field] = value
        raw = json.dumps(data, ensure_ascii=False)
        with pytest.raises(ValueError, match=pattern):
            ob.load(raw)


def test_load_rejects_bad_json() -> None:
    """语法错误、非对象与非法编码一律拒绝。"""
    with pytest.raises(ValueError, match="not valid JSON"):
        ob.load("{")
    with pytest.raises(ValueError, match="JSON object"):
        ob.load("[]")
    with pytest.raises(ValueError, match="UTF-8"):
        ob.load(b"\xff\xfe")
    with pytest.raises(TypeError, match="str or bytes"):
        ob.load(42)  # type: ignore[arg-type]


def test_load_rejects_bad_fields() -> None:
    """未知、缺失与类型不符的字段一律拒绝。"""
    base = record_of()
    with pytest.raises(ValueError, match="unknown prepare fields"):
        ob.load(json.dumps({**base, "extra": 1}))
    with pytest.raises(ValueError, match="missing prepare fields"):
        ob.load(json.dumps({"key": "sw_64"}))
    with pytest.raises(ValueError, match="unregistered"):
        ob.load(json.dumps({**base, "key": "riscv"}))
    with pytest.raises(ValueError, match="key must be a string"):
        ob.load(json.dumps({**base, "key": 1}))
    with pytest.raises(ValueError, match="platform must be a string"):
        ob.load(json.dumps({**base, "platform": 1}))
    with pytest.raises(ValueError, match="JSON array"):
        ob.load(json.dumps({**base, "exps": "E1-02"}))
    with pytest.raises(ValueError, match="exps must be a string"):
        ob.load(json.dumps({**base, "exps": [1]}))


def test_verify_accepts_blocked() -> None:
    """登记在册且仅有准备证据的清单互证通过。"""
    assert ob.verify(oe.Manifest(DATE, HOST, IMAGE, blocked())) is True


def test_verify_rejects_other_verdict() -> None:
    """非 BLOCKED 判定不参与降级互证。"""
    result = blocked()
    result.verdict = om.Verdict.PASS
    result.blocked_reason = ""
    assert ob.verify(oe.Manifest(DATE, HOST, IMAGE, result)) is False


def test_verify_rejects_unknown_reason() -> None:
    """降级原因未登记即拒绝。"""
    result = blocked(reason="任意理由")
    assert ob.verify(oe.Manifest(DATE, HOST, IMAGE, result)) is False


def test_verify_rejects_extra_step() -> None:
    """出现执行步的证据包不是诚实降级。"""
    steps = (om.Step.PREPARE, om.Step.RUN)
    assert ob.verify(oe.Manifest(DATE, HOST, IMAGE, blocked(steps=steps))) \
        is False


def test_verify_rejects_other_exp() -> None:
    """实验不在登记影响面内即拒绝。"""
    result = blocked(exp_id="E1-03")
    assert ob.verify(oe.Manifest(DATE, HOST, IMAGE, result)) is False


def test_verify_rejects_types() -> None:
    """互证输入必须是清单。"""
    with pytest.raises(TypeError, match="Manifest"):
        ob.verify(object())  # type: ignore[arg-type]


def test_verify_accepts_sealed(tmp_path) -> None:
    """真实封口的降级包可被反查为诚实登记。"""
    store = os_.Store(tmp_path / "evidence")
    man = seal(store)
    assert man.result.verdict is om.Verdict.BLOCKED
    assert ob.verify(man) is True
    assert store.verify_all() == 1


def test_ledger_lists_blocked() -> None:
    """台账只列 BLOCKED，并按实验编号排序。"""
    results = [
        ob.apply(prepared("E3-03"), "m2-smp"),
        ob.apply(prepared("E1-04"), "loongarch"),
        ob.apply(prepared("E1-02"), "sw_64"),
        prepared("E1-03"),
    ]
    assert ob.ledger(results) == (
        ("E1-02", "sw_64"),
        ("E1-04", "loongarch"),
        ("E3-03", "m2-smp"),
    )
    assert ob.ledger([]) == ()


def test_ledger_rejects_unregistered() -> None:
    """未登记原因或影响面不符的 BLOCKED 不得入账。"""
    with pytest.raises(ValueError, match="unregistered"):
        ob.ledger([blocked(reason="任意理由")])
    with pytest.raises(ValueError, match="unregistered"):
        ob.ledger([blocked(exp_id="E1-03")])


def test_ledger_rejects_types() -> None:
    """汇总输入必须全是实验结果对象。"""
    with pytest.raises(TypeError, match="ExpResult"):
        ob.ledger([object()])  # type: ignore[list-item]


def test_judge_accepts_blocked() -> None:
    """判定器与登记入口对 BLOCKED 的证据要求一致。"""
    assert oj.PackJudge().decide(
        ob.apply(prepared(), "sw_64")) is om.Verdict.BLOCKED
    assert oj.PackJudge().decide(ran()) is om.Verdict.FAIL
