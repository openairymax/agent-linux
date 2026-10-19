# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_model 单元测试：数据模型构造校验与判定接口契约。"""

from __future__ import annotations

import pytest

import osaccrt_model as om

DIGEST = "a" * 64
SIG_A = "lidecheng@spharx.cn"
SIG_B = "wangliren@spharx.cn"


def make_th(key: str = "boot.time") -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold(key, "OS-ACC-BOOT-010", "60", "s")


def make_ev(step: om.Step = om.Step.PREPARE) -> om.Evidence:
    """构造步骤匹配的合法证据条目。"""
    return om.Evidence(f"step-01-{step.value}.json", DIGEST, step)


def test_verdict_four_states() -> None:
    """判定四态与 OS-ACC-RT §3.2 一一对应。"""
    assert [item.value for item in om.Verdict] == [
        "PASS",
        "FAIL",
        "WAIVED",
        "BLOCKED",
    ]


def test_step_six_steps() -> None:
    """证据包六步顺序固定。"""
    assert [item.value for item in om.Step] == [
        "prepare",
        "run",
        "capture",
        "verdict",
        "review",
        "archive",
    ]


def test_threshold_accepts_traceable_source() -> None:
    """阈值字段逐条可追溯。"""
    th = make_th()
    assert (th.source, th.limit, th.unit) == ("OS-ACC-BOOT-010", "60", "s")


@pytest.mark.parametrize("field", ["key", "source", "limit", "unit"])
def test_threshold_rejects_empty(field: str) -> None:
    """阈值任一字段为空即拒绝构造。"""
    values = {"key": "k", "source": "s", "limit": "1", "unit": "s"}
    values[field] = ""
    with pytest.raises(ValueError, match=field):
        om.Threshold(**values)


def test_evidence_accepts_pack_relative_path() -> None:
    """证据路径为包内相对路径。"""
    ev = make_ev(om.Step.CAPTURE)
    assert ev.path == "step-01-capture.json"
    assert ev.step is om.Step.CAPTURE


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../escape.json", "step-01//x.json", "./x.json"],
)
def test_evidence_rejects_non_normalized_path(path: str) -> None:
    """绝对路径、向上逃逸与未归一化路径均拒绝。"""
    with pytest.raises(ValueError):
        om.Evidence(path, DIGEST, om.Step.RUN)


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_evidence_rejects_bad_digest(digest: str) -> None:
    """sha256 必须为 64 位小写十六进制。"""
    with pytest.raises(ValueError, match="sha256"):
        om.Evidence("step-01-run.json", digest, om.Step.RUN)


def test_evidence_rejects_bad_step() -> None:
    """步骤必须取自 Step 枚举。"""
    with pytest.raises(ValueError, match="step"):
        om.Evidence("step-01-run.json", DIGEST, "run")  # type: ignore[arg-type]


def test_result_defaults_to_undecided() -> None:
    """新实验结果尚无判定与证据。"""
    exp = om.ExpResult("exp-20260119-001", (make_th(),))
    assert exp.verdict is None
    assert exp.evidences == []
    assert exp.keys == ("boot.time",)


def test_result_evidence_lists_are_independent() -> None:
    """默认证据列表不得在实例间共享。"""
    first = om.ExpResult("exp-1", (make_th(),))
    second = om.ExpResult("exp-2", (make_th(),))
    first.evidences.append(make_ev())
    assert second.evidences == []


def test_result_steps_deduplicate_in_order() -> None:
    """步骤序列按首次出现去重。"""
    exp = om.ExpResult("exp-1", (make_th(),))
    exp.evidences.extend(
        [make_ev(om.Step.RUN), make_ev(om.Step.PREPARE), make_ev(om.Step.RUN)]
    )
    assert exp.steps == (om.Step.RUN, om.Step.PREPARE)


@pytest.mark.parametrize(
    "exp_id", ["", "-lead", "with space", "with/slash", ".", "中文"]
)
def test_result_rejects_bad_exp_id(exp_id: str) -> None:
    """实验编号必须为字母数字开头的路径安全标识。"""
    with pytest.raises(ValueError, match="exp_id"):
        om.ExpResult(exp_id, (make_th(),))


def test_result_rejects_empty_thresholds() -> None:
    """阈值表不能为空。"""
    with pytest.raises(ValueError, match="thresholds"):
        om.ExpResult("exp-1", ())


def test_result_rejects_duplicate_thresholds() -> None:
    """同一阈值键不得重复登记。"""
    with pytest.raises(ValueError, match="duplicate"):
        om.ExpResult("exp-1", (make_th(), make_th()))


def test_result_rejects_foreign_evidence() -> None:
    """证据列表只接受 Evidence 实例。"""
    exp = om.ExpResult("exp-1", (make_th(),))
    exp.evidences.append("step-01-run.json")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Evidence"):
        exp.__post_init__()


def test_waiver_requires_two_signers() -> None:
    """WAIVED 必须双签且签署人互异。"""
    th = (make_th(),)
    with pytest.raises(ValueError, match="waiver_sig"):
        om.ExpResult("exp-1", th, verdict=om.Verdict.WAIVED)
    with pytest.raises(ValueError, match="distinct"):
        om.ExpResult(
            "exp-1", th, verdict=om.Verdict.WAIVED, waiver_sig=(SIG_A, SIG_A)
        )
    with pytest.raises(ValueError, match="email"):
        om.ExpResult(
            "exp-1", th, verdict=om.Verdict.WAIVED, waiver_sig=(SIG_A, "nobody")
        )
    exp = om.ExpResult(
        "exp-1", th, verdict=om.Verdict.WAIVED, waiver_sig=(SIG_A, SIG_B)
    )
    assert exp.waiver_sig == (SIG_A, SIG_B)


def test_waiver_forbidden_outside_waived() -> None:
    """非 WAIVED 状态不得携带双签。"""
    with pytest.raises(ValueError, match="WAIVED"):
        om.ExpResult(
            "exp-1", (make_th(),), verdict=om.Verdict.PASS, waiver_sig=(SIG_A, SIG_B)
        )


def test_blocked_requires_reason() -> None:
    """BLOCKED 必须记录降级原因。"""
    with pytest.raises(ValueError, match="blocked_reason"):
        om.ExpResult("exp-1", (make_th(),), verdict=om.Verdict.BLOCKED)
    exp = om.ExpResult(
        "exp-1",
        (make_th(),),
        verdict=om.Verdict.BLOCKED,
        blocked_reason="no bare metal available",
    )
    assert exp.verdict is om.Verdict.BLOCKED


def test_blocked_reason_forbidden_outside_blocked() -> None:
    """非 BLOCKED 状态不得携带降级原因。"""
    with pytest.raises(ValueError, match="blocked_reason"):
        om.ExpResult("exp-1", (make_th(),), verdict=om.Verdict.FAIL, blocked_reason="x")


def test_judge_protocol_declares_decide() -> None:
    """判定器协议只声明 decide 一个入口。"""
    assert "decide" in om.Judge.__dict__
