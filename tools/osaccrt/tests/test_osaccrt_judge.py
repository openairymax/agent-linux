# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_judge 单元测试：四态判定状态机与逐阈值对账。

覆盖证据缺失、阈值无产物、判定声明与证据不符、WAIVED 双签门禁、
BLOCKED 仅准备步可进入等边界，确保判定器只读证据包。
"""

from __future__ import annotations

import pytest

import osaccrt_judge as oj
import osaccrt_model as om

DIGEST = "d" * 64
SIG_A = "lidecheng@spharx.cn"
SIG_B = "wangliren@spharx.cn"
JUDGE = oj.PackJudge()


def ths(*keys: str) -> tuple[om.Threshold, ...]:
    """构造逐条引自 NFR 原文的阈值表。"""
    return tuple(om.Threshold(key, "NFR-P-005", "30", "s") for key in keys)


def ev(path: str, step: om.Step) -> om.Evidence:
    """构造步骤匹配的合法证据条目。"""
    return om.Evidence(path, DIGEST, step)


def full(key: str = "boot.time") -> list[om.Evidence]:
    """构造三步齐全且阈值有同名产物的证据链。"""
    return [
        ev("step-01-prepare.json", om.Step.PREPARE),
        ev("step-02-run.log", om.Step.RUN),
        ev(f"step-03-artifacts/{key}.json", om.Step.CAPTURE),
    ]


def test_empty_pack_fails() -> None:
    """无任何证据的实验直接 FAIL。"""
    exp = om.ExpResult("exp-001", ths("boot.time"))
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_prepare_only_fails() -> None:
    """只有准备证据不足以判定 PASS。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"),
        [ev("step-01-prepare.json", om.Step.PREPARE)],
    )
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_full_pack_passes() -> None:
    """三步齐全且阈值有产物即 PASS。"""
    exp = om.ExpResult("exp-001", ths("boot.time"), full())
    assert JUDGE.decide(exp) is om.Verdict.PASS


def test_capture_dir_covers_key() -> None:
    """阈值产物可落在同名目录下。"""
    evs = full()
    evs[-1] = ev("step-03-artifacts/boot.time/report.json", om.Step.CAPTURE)
    exp = om.ExpResult("exp-001", ths("boot.time"), evs)
    assert JUDGE.decide(exp) is om.Verdict.PASS


def test_missing_threshold_fails() -> None:
    """任一阈值缺产物即 FAIL。"""
    exp = om.ExpResult("exp-001", ths("boot.time", "login.time"), full())
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_foreign_artifact_fails() -> None:
    """产物名与阈值键不一致视为未对账。"""
    exp = om.ExpResult("exp-001", ths("boot.time"), full("login.time"))
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_missing_keys_reports_order() -> None:
    """缺口按阈值表顺序报告。"""
    exp = om.ExpResult("exp-001", ths("a", "b"), full("a"))
    assert oj.missing_keys(exp) == ("b",)


def test_claim_pass_ok() -> None:
    """证据支持时 PASS 声明被接受。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"), full(), verdict=om.Verdict.PASS
    )
    assert JUDGE.decide(exp) is om.Verdict.PASS


def test_claim_pass_rejected() -> None:
    """证据不支持时 PASS 声明降为 FAIL。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"), verdict=om.Verdict.PASS
    )
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_claim_fail_stays_fail() -> None:
    """FAIL 是合法结论，即使证据完备也不改写。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"), full(), verdict=om.Verdict.FAIL
    )
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_waived_ok() -> None:
    """证据完备且双签时 WAIVED 成立。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"), full(),
        verdict=om.Verdict.WAIVED, waiver_sig=(SIG_A, SIG_B),
    )
    assert JUDGE.decide(exp) is om.Verdict.WAIVED


def test_waived_incomplete_fails() -> None:
    """证据不全时不得以双签换取 WAIVED。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"), full()[:1],
        verdict=om.Verdict.WAIVED, waiver_sig=(SIG_A, SIG_B),
    )
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_blocked_ok() -> None:
    """准备步登记实机缺失即为 BLOCKED。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"),
        [ev("step-01-prepare.json", om.Step.PREPARE)],
        verdict=om.Verdict.BLOCKED, blocked_reason="no sw_64 host",
    )
    assert JUDGE.decide(exp) is om.Verdict.BLOCKED


def test_blocked_without_prepare_fails() -> None:
    """缺少准备证据的 BLOCKED 不予承认。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"),
        verdict=om.Verdict.BLOCKED, blocked_reason="no sw_64 host",
    )
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_blocked_after_run_fails() -> None:
    """已执行却声明 BLOCKED 属于降级不诚实。"""
    exp = om.ExpResult(
        "exp-001", ths("boot.time"),
        [ev("step-01-prepare.json", om.Step.PREPARE),
         ev("step-02-run.log", om.Step.RUN)],
        verdict=om.Verdict.BLOCKED, blocked_reason="no sw_64 host",
    )
    assert JUDGE.decide(exp) is om.Verdict.FAIL


def test_reject_non_result() -> None:
    """判定器只接受实验结果对象。"""
    with pytest.raises(TypeError):
        JUDGE.decide(None)


def test_decide_is_idempotent() -> None:
    """同一证据包重复判定结果一致。"""
    exp = om.ExpResult("exp-001", ths("boot.time"), full())
    assert JUDGE.decide(exp) is JUDGE.decide(exp)
