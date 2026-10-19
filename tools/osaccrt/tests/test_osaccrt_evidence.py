# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_evidence 单元测试：清单序列化、步骤落位与 sha256 链封口。"""

from __future__ import annotations

import json

import pytest

import osaccrt_evidence as oe
import osaccrt_model as om

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
SIG_A = "lidecheng@spharx.cn"
SIG_B = "wangliren@spharx.cn"
RUN_EV = om.Evidence("step-02-run.log", DIGEST_A, om.Step.RUN)


def make_th() -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold("boot.time", "OS-ACC-BOOT-010", "60", "s")


def make_result(
    evidences: tuple[om.Evidence, ...] = (RUN_EV,)
) -> om.ExpResult:
    """构造携带默认运行证据的实验结果。"""
    exp = om.ExpResult("exp-001", (make_th(),))
    exp.evidences.extend(evidences)
    return exp


def make_manifest(**kwargs: object) -> oe.Manifest:
    """构造最小合法清单，允许逐项覆盖。"""
    fields: dict[str, object] = {
        "date": "2026-01-19",
        "host": "qemu-x86_64",
        "image_sha256": DIGEST_B,
        "result": make_result(),
    }
    fields.update(kwargs)
    return oe.Manifest(**fields)  # type: ignore[arg-type]


def test_layout_matches_spec() -> None:
    """六步产物文件名与草案 §3.4 一致。"""
    assert oe.STEP_LAYOUT[om.Step.PREPARE] == "step-01-prepare.json"
    assert oe.STEP_LAYOUT[om.Step.RUN] == "step-02-run.log"
    assert oe.STEP_LAYOUT[om.Step.CAPTURE] == "step-03-artifacts"
    assert oe.STEP_LAYOUT[om.Step.ARCHIVE] == "chain.sha256"


def test_chain_empty_returns_prev() -> None:
    """空证据集不产生新链头。"""
    assert oe.chain_head("exp-001", ()) == ""
    assert oe.chain_head("exp-001", (), DIGEST_A) == DIGEST_A


def test_chain_is_deterministic_digest() -> None:
    """同一证据序列得到同一 64 位摘要。"""
    first = oe.chain_head("exp-001", (RUN_EV,))
    second = oe.chain_head("exp-001", (RUN_EV,))
    assert first == second
    assert len(first) == 64


def test_chain_binds_order() -> None:
    """证据顺序不同则链头不同。"""
    prepare = om.Evidence("step-01-prepare.json", DIGEST_B, om.Step.PREPARE)
    forward = oe.chain_head("exp-001", (RUN_EV, prepare))
    backward = oe.chain_head("exp-001", (prepare, RUN_EV))
    assert forward != backward


def test_chain_binds_experiment() -> None:
    """跨实验拼接同组证据会改变链头。"""
    head = oe.chain_head("exp-001", (RUN_EV,))
    assert head != oe.chain_head("exp-002", (RUN_EV,))


def test_chain_binds_prev_head() -> None:
    """前链头参与计算，断链可被发现。"""
    assert oe.chain_head("exp-001", (RUN_EV,)) != oe.chain_head(
        "exp-001", (RUN_EV,), DIGEST_A
    )


def test_chain_binds_metadata() -> None:
    """清单元数据参与计算，篡改宿主会改变链头。"""
    assert make_manifest(host="other").chain_head != make_manifest().chain_head


def test_chain_binds_verdict() -> None:
    """判定变化必须改变链头，避免事后改写结论。"""
    exp = make_result()
    exp.verdict = om.Verdict.PASS
    assert make_manifest(result=exp).chain_head != make_manifest().chain_head


def test_seal_rejects_empty_pack() -> None:
    """空证据包不得封口。"""
    with pytest.raises(ValueError, match="empty"):
        oe.seal(make_manifest(result=make_result(())))


def test_seal_and_verify_roundtrip() -> None:
    """封口摘要可复核，篡改证据后复核失败。"""
    man = make_manifest()
    sealed = oe.seal(man)
    assert oe.verify(man, sealed)
    assert not oe.verify(man, DIGEST_A)


def test_verify_rejects_empty_pack() -> None:
    """空包即使摘要匹配也不复核通过。"""
    man = make_manifest(result=make_result(()))
    assert not oe.verify(man, "")


def test_dump_load_roundtrip_pending() -> None:
    """未判定清单可无损往返。"""
    man = make_manifest()
    assert oe.load(oe.dump(man)) == man


def test_dump_load_roundtrip_waived() -> None:
    """双签与判定字段可无损往返。"""
    exp = make_result()
    exp.verdict = om.Verdict.WAIVED
    exp.waiver_sig = (SIG_A, SIG_B)
    man = make_manifest(result=exp, supersedes="exp-000")
    assert oe.load(oe.dump(man)) == man


def test_dump_load_roundtrip_blocked() -> None:
    """降级原因可无损往返。"""
    exp = make_result()
    exp.verdict = om.Verdict.BLOCKED
    exp.blocked_reason = "no sw_64 bare metal"
    text = oe.dump(make_manifest(result=exp))
    assert oe.load(text) == make_manifest(result=exp)


def test_dump_is_canonical() -> None:
    """输出键排序且以换行结尾。"""
    text = oe.dump(make_manifest())
    assert text.endswith("\n")
    assert text.index('"date"') < text.index('"exp_id"')


def test_load_rejects_unknown_field() -> None:
    """未知字段意味着 schema 漂移，直接拒绝。"""
    with pytest.raises(ValueError, match="unknown"):
        oe.load('{"exp_id": "exp-1", "extra": 1}')


def test_load_rejects_missing_field() -> None:
    """缺失必填字段直接拒绝。"""
    with pytest.raises(ValueError, match="missing"):
        oe.load('{"exp_id": "exp-1"}')


@pytest.mark.parametrize("text", ["", "[]", "{bad", '{"a": 1}'])
def test_load_rejects_bad_json(text: str) -> None:
    """非 JSON 对象一律拒绝。"""
    with pytest.raises(ValueError):
        oe.load(text)


@pytest.mark.parametrize(
    "value", ["2026-1-9", "20260119", "2026-02-30", "2026/01/19", ""]
)
def test_manifest_rejects_bad_date(value: str) -> None:
    """日期必须是零填充的合法日历日。"""
    with pytest.raises(ValueError, match="date"):
        make_manifest(date=value)


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, ""])
def test_manifest_rejects_bad_image_digest(value: str) -> None:
    """镜像摘要必须是 64 位小写十六进制。"""
    with pytest.raises(ValueError, match="image_sha256"):
        make_manifest(image_sha256=value)


def test_manifest_rejects_bad_prev_head() -> None:
    """前链头只能为空或合法摘要。"""
    with pytest.raises(ValueError, match="prev_chain_head"):
        make_manifest(prev_chain_head="deadbeef")


def test_manifest_rejects_self_supersede() -> None:
    """supersedes 不得指向自身。"""
    with pytest.raises(ValueError, match="another"):
        make_manifest(supersedes="exp-001")


def test_manifest_rejects_bad_supersede() -> None:
    """supersedes 必须是路径安全编号。"""
    with pytest.raises(ValueError, match="supersedes"):
        make_manifest(supersedes="../exp-000")


def test_manifest_rejects_misfiled_evidence() -> None:
    """步骤与文件名不匹配即拒绝。"""
    stray = om.Evidence("step-02-run.log", DIGEST_A, om.Step.PREPARE)
    with pytest.raises(ValueError, match="prepare"):
        make_manifest(result=make_result((stray,)))


def test_manifest_accepts_capture_child() -> None:
    """采集产物必须位于 step-03-artifacts 目录内。"""
    item = om.Evidence(
        "step-03-artifacts/report.json", DIGEST_A, om.Step.CAPTURE
    )
    assert make_manifest(result=make_result((item,))).exp_id == "exp-001"


def test_manifest_rejects_capture_root() -> None:
    """目录本身不能作为采集证据。"""
    item = om.Evidence("step-03-artifacts", DIGEST_A, om.Step.CAPTURE)
    with pytest.raises(ValueError, match="step-03-artifacts"):
        make_manifest(result=make_result((item,)))


def test_manifest_rejects_archive_evidence() -> None:
    """封口文件不进入证据列表，避免自指。"""
    item = om.Evidence("chain.sha256", DIGEST_A, om.Step.ARCHIVE)
    with pytest.raises(ValueError, match="ARCHIVE"):
        make_manifest(result=make_result((item,)))


def test_manifest_rejects_duplicate_path() -> None:
    """同一路径不得重复登记。"""
    twin = om.Evidence("step-02-run.log", DIGEST_B, om.Step.RUN)
    with pytest.raises(ValueError, match="duplicate"):
        make_manifest(result=make_result((RUN_EV, twin)))


def test_load_rejects_bad_verdict() -> None:
    """判定值必须属于四态或 null。"""
    text = oe.dump(make_manifest())
    text = text.replace('"verdict": null', '"verdict": "OK"')
    with pytest.raises(ValueError, match="verdict"):
        oe.load(text)


def test_load_rejects_bad_step() -> None:
    """证据步骤必须是六步之一。"""
    text = oe.dump(make_manifest()).replace('"step": "run"', '"step": "boot"')
    with pytest.raises(ValueError, match="step"):
        oe.load(text)


@pytest.mark.parametrize("value", ["abc", ["a@b", "a@b", "c@d"], [1, 2]])
def test_load_rejects_bad_waiver_shape(value: object) -> None:
    """双签必须是二元邮箱数组。"""
    exp = make_result()
    exp.verdict = om.Verdict.WAIVED
    exp.waiver_sig = (SIG_A, SIG_B)
    raw = json.loads(oe.dump(make_manifest(result=exp)))
    raw["waiver_sig"] = value
    with pytest.raises(ValueError, match="waiver_sig"):
        oe.load(json.dumps(raw))
