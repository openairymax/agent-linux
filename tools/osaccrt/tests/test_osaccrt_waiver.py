# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_waiver 单元测试：双签豁免、复核记录与清单互证。"""

from __future__ import annotations

import json

import pytest

import osaccrt_evidence as oe
import osaccrt_model as om
import osaccrt_store as os_
import osaccrt_waiver as ow

DATE = "2026-01-19"
IMAGE = "b" * 64
HOST = "qemu-x86_64"
PREP = "step-01-prepare.json"
RUN = "step-02-run.log"
CAP = "step-03-artifacts"
REVIEW = "step-05-review.json"
SIG = ("decheng@spharx.cn", "liren@spharx.cn")


def make_th(key: str = "boot.time") -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold(key, "OS-ACC-BOOT-010", "60", "s")


def make_fail(exp_id: str = "exp-001",
              verdict: om.Verdict | None = om.Verdict.FAIL) -> om.ExpResult:
    """构造带三条证据的实验结果。"""
    result = om.ExpResult(exp_id, (make_th(),))
    for rel, step in (
        (PREP, om.Step.PREPARE),
        (RUN, om.Step.RUN),
        (f"{CAP}/boot.time", om.Step.CAPTURE),
    ):
        result.evidences.append(om.Evidence(rel, "a" * 64, step))
    result.verdict = verdict
    return result


def make_waiver(basis: ow.Basis = ow.Basis.ACCEPTED,
                evidence: str = "",
                signers: tuple[str, str] = SIG) -> ow.Waiver:
    """构造合法豁免结论。"""
    return ow.Waiver(basis, "仅影响日志格式", evidence, signers)


def test_apply_fail_to_waived() -> None:
    """FAIL 经双签转为 WAIVED，原结果不被修改。"""
    result = make_fail()
    waived = ow.apply(result, make_waiver())
    assert waived.verdict is om.Verdict.WAIVED
    assert waived.waiver_sig == SIG
    assert result.verdict is om.Verdict.FAIL
    assert waived is not result


def test_apply_requires_fail() -> None:
    """只有 FAIL 可以豁免；未判定、PASS 与已豁免一律拒绝。"""
    for verdict in (None, om.Verdict.PASS, om.Verdict.WAIVED):
        with pytest.raises(ValueError, match="only FAIL"):
            ow.apply(make_fail(verdict=verdict), make_waiver())


def test_apply_rejects_types() -> None:
    """结果与豁免结论都必须是本模块类型。"""
    with pytest.raises(TypeError, match="ExpResult"):
        ow.apply(object(), make_waiver())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Waiver"):
        ow.apply(make_fail(), object())  # type: ignore[arg-type]


def test_equivalent_requires_pack_evidence() -> None:
    """等价替代证据必须已在包内取证。"""
    missing = make_waiver(ow.Basis.EQUIVALENT, "step-03-artifacts/other")
    with pytest.raises(ValueError, match="not in pack"):
        ow.apply(make_fail(), missing)


def test_equivalent_accepts_pack_evidence() -> None:
    """引用包内证据的等价豁免可通过。"""
    item = make_waiver(ow.Basis.EQUIVALENT, f"{CAP}/boot.time")
    assert ow.apply(make_fail(), item).verdict is om.Verdict.WAIVED


def test_basis_shape_rules() -> None:
    """等价依据必填相对路径，评审可接受不得附带证据。"""
    with pytest.raises(ValueError, match="requires evidence"):
        ow.Waiver(ow.Basis.EQUIVALENT, "理由", "", SIG)
    with pytest.raises(ValueError, match="must not carry"):
        ow.Waiver(ow.Basis.ACCEPTED, "理由", f"{CAP}/boot.time", SIG)
    with pytest.raises(ValueError, match="path"):
        ow.Waiver(ow.Basis.EQUIVALENT, "理由", "/etc/passwd", SIG)
    with pytest.raises(ValueError, match="normalized"):
        ow.Waiver(ow.Basis.EQUIVALENT, "理由", "../escape", SIG)
    with pytest.raises(ValueError, match="basis"):
        ow.Waiver("accepted", "理由", "", SIG)  # type: ignore[arg-type]


def test_signers_must_be_distinct() -> None:
    """双签必须是两个互异邮箱。"""
    with pytest.raises(ValueError, match="distinct"):
        ow.Waiver(ow.Basis.ACCEPTED, "理由", "", ("a@x", "a@x"))
    with pytest.raises(ValueError, match="email"):
        ow.Waiver(ow.Basis.ACCEPTED, "理由", "", ("a", "b@x"))
    with pytest.raises(ValueError, match="two signers"):
        ow.Waiver(ow.Basis.ACCEPTED, "理由", "", ("a@x",))
    with pytest.raises(ValueError, match="two signers"):
        ow.Waiver(ow.Basis.ACCEPTED, "理由", "", "a@x")


def test_signers_normalized() -> None:
    """列表形式的双签归一化为元组，保证相等与互证一致。"""
    item = ow.Waiver(ow.Basis.ACCEPTED, "理由", "", ["a@x", "b@y"])
    assert item.signers == ("a@x", "b@y")
    assert ow.load(ow.record(item)) == item


def test_reason_must_be_single_line() -> None:
    """豁免理由必须是单行可见文本。"""
    with pytest.raises(ValueError, match="single visible line"):
        ow.Waiver(ow.Basis.ACCEPTED, "两行\n理由", "", SIG)
    with pytest.raises(ValueError, match="reason"):
        ow.Waiver(ow.Basis.ACCEPTED, "", "", SIG)


def test_record_accepted_layout() -> None:
    """评审可接受的复核记录不含证据字段。"""
    raw = ow.record(make_waiver())
    data = json.loads(raw)
    assert data == {
        "basis": "accepted",
        "reason": "仅影响日志格式",
        "signers": list(SIG),
    }
    assert raw.endswith(b"\n")


def test_record_equivalent_layout() -> None:
    """等价豁免的复核记录携带包内证据路径。"""
    item = make_waiver(ow.Basis.EQUIVALENT, f"{CAP}/boot.time")
    data = json.loads(ow.record(item))
    assert data["evidence"] == f"{CAP}/boot.time"


def test_record_rejects_non_waiver() -> None:
    """序列化只接受 Waiver。"""
    with pytest.raises(TypeError, match="Waiver"):
        ow.record(object())  # type: ignore[arg-type]


def test_load_roundtrip() -> None:
    """文本与字节两种输入都能还原一致结论。"""
    for basis, evidence in (
        (ow.Basis.ACCEPTED, ""),
        (ow.Basis.EQUIVALENT, f"{CAP}/boot.time"),
    ):
        item = make_waiver(basis, evidence)
        raw = ow.record(item)
        assert ow.load(raw) == item
        assert ow.load(raw.decode("utf-8")) == item


def test_load_rejects_bad_json() -> None:
    """语法错误、非对象与非法编码一律拒绝。"""
    with pytest.raises(ValueError, match="not valid JSON"):
        ow.load("{")
    with pytest.raises(ValueError, match="JSON object"):
        ow.load("[]")
    with pytest.raises(ValueError, match="UTF-8"):
        ow.load(b"\xff\xfe")
    with pytest.raises(TypeError, match="str or bytes"):
        ow.load(42)  # type: ignore[arg-type]


def test_load_rejects_bad_fields() -> None:
    """未知、缺失与类型不符的字段一律拒绝。"""
    base = {"basis": "accepted", "reason": "理由", "signers": list(SIG)}
    with pytest.raises(ValueError, match="review has unknown fields"):
        ow.load(json.dumps({**base, "extra": 1}))
    with pytest.raises(ValueError, match="review is missing fields"):
        ow.load(json.dumps({"basis": "accepted"}))
    with pytest.raises(ValueError, match="invalid waiver basis"):
        ow.load(json.dumps({**base, "basis": "waived"}))
    with pytest.raises(ValueError, match="reason must be a string"):
        ow.load(json.dumps({**base, "reason": 1}))
    with pytest.raises(ValueError, match="two-element array"):
        ow.load(json.dumps({**base, "signers": ["a@x"]}))
    with pytest.raises(ValueError, match="signers must be a string"):
        ow.load(json.dumps({**base, "signers": [1, 2]}))


def test_verify_accepts_matching_pack() -> None:
    """已封口清单与复核结论一致时互证通过。"""
    item = make_waiver()
    man = _seal_waived(item)
    assert ow.verify(man, item) is True


def test_verify_rejects_mismatch() -> None:
    """签署人、判定与复核证据任一不符都不通过。"""
    item = make_waiver()
    man = _seal_waived(item)
    other = make_waiver(signers=("a@x", "b@y"))
    assert ow.verify(man, other) is False
    failed = _seal_waived(item, verdict=om.Verdict.FAIL)
    assert ow.verify(failed, item) is False
    assert ow.verify(_seal_waived(item, review=False), item) is False


def test_verify_rejects_absent_evidence() -> None:
    """等价证据未入包时互证失败。"""
    item = make_waiver(ow.Basis.EQUIVALENT, f"{CAP}/boot.time")
    man = _seal_waived(item)
    lost = make_waiver(ow.Basis.EQUIVALENT, "step-03-artifacts/other")
    assert ow.verify(man, lost) is False


def test_verify_rejects_types() -> None:
    """互证输入必须是清单与豁免结论。"""
    with pytest.raises(TypeError, match="Manifest"):
        ow.verify(object(), make_waiver())
    man = _seal_waived(make_waiver())
    with pytest.raises(TypeError, match="Waiver"):
        ow.verify(man, object())


def test_sealed_waiver_roundtrip(tmp_path) -> None:
    """豁免结论随证据包封口，重新加载后双签与判定一致。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, "exp-001")
    result = om.ExpResult("exp-001", (make_th(),))
    for rel, step in (
        (PREP, om.Step.PREPARE),
        (RUN, om.Step.RUN),
        (f"{CAP}/boot.time", om.Step.CAPTURE),
    ):
        digest = store.write(DATE, "exp-001", rel, b"x\n")
        result.evidences.append(om.Evidence(rel, digest, step))
    result.verdict = om.Verdict.FAIL
    item = make_waiver(ow.Basis.EQUIVALENT, f"{CAP}/boot.time")
    waived = ow.apply(result, item)
    digest = store.write(DATE, "exp-001", REVIEW, ow.record(item))
    waived.evidences.append(om.Evidence(REVIEW, digest, om.Step.REVIEW))
    store.seal(oe.Manifest(DATE, HOST, IMAGE, waived))
    man = store.load(DATE, "exp-001")
    assert man.result.verdict is om.Verdict.WAIVED
    assert man.result.waiver_sig == SIG
    assert ow.verify(man, item) is True
    assert store.verify_all() == 1


def _seal_waived(item: ow.Waiver, verdict: om.Verdict = om.Verdict.WAIVED,
                 review: bool = True) -> oe.Manifest:
    """构造只用于互证的清单，不涉及落盘。"""
    result = make_fail()
    result.verdict = verdict
    if verdict is om.Verdict.WAIVED:
        result.waiver_sig = item.signers
    if review:
        result.evidences.append(
            om.Evidence(REVIEW, "c" * 64, om.Step.REVIEW))
    return oe.Manifest(DATE, HOST, IMAGE, result)
