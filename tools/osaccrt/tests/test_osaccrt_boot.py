# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_boot 单元测试：标记文法、三种结论态与负向证据复用。"""

from __future__ import annotations

import pytest

import osaccrt_boot as ob

HEALTH = "[    5.104237] desktop_ready.sh[563]: AIRYMAX-BOOT-HEALTH failed=0"


def test_mark_with_kernel_stamp() -> None:
    """内核实秒与程序名前缀被剥离，字段逐一还原。"""
    log = ob.parse_text(HEALTH)
    mark = log.last(ob.TAG_HEALTH)
    assert mark is not None
    assert mark.tag == ob.TAG_HEALTH
    assert mark.fields == (("failed", "0"),)
    assert log.secs(ob.TAG_HEALTH) == 5.104237


def test_mark_without_stamp() -> None:
    """无内核前缀的标记同样解析，秒数为未知。"""
    log = ob.parse_text("AIRYMAX-BOOT-HEALTH failed=1")
    assert log.last(ob.TAG_HEALTH).get("failed") == "1"  # type: ignore[union-attr]
    assert log.secs(ob.TAG_HEALTH) is None


def test_ready_fields() -> None:
    """桌面就绪标记带 unit/class/type 三字段。"""
    line = (
        "[    7.178580] desktop_ready.sh[563]: "
        "AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11"
    )
    mark = ob.parse_text(line).last(ob.TAG_READY)
    assert mark is not None
    assert mark.get("unit") == "lightdm"
    assert mark.get("class") == "greeter"
    assert mark.get("type") == "x11"
    assert mark.get("absent", "none") == "none"


def test_degraded_units() -> None:
    """降级标记的单元清单保持原样，不按逗号拆分。"""
    log = ob.parse_text("AIRYMAX-BOOT-DEGRADED failed=2 units=a.service,b.service")
    mark = log.last(ob.TAG_DEGRADED)
    assert mark is not None
    assert mark.get("failed") == "2"
    assert mark.get("units") == "a.service,b.service"


def test_smoke_three_states() -> None:
    """会话自报结论区分通过、失败与未上报。"""
    assert ob.parse_text("AIRYMAX-SMOKE-PASS tap=/var/log/s.tap").smoke() is True
    assert ob.parse_text("AIRYMAX-SMOKE-FAIL reason=tap tap=/x").smoke() is False
    assert ob.parse_text(HEALTH).smoke() is None


def test_smoke_fail_wins() -> None:
    """同一会话同时出现两种结论时按失败处理。"""
    text = "AIRYMAX-SMOKE-PASS tap=/x\nAIRYMAX-SMOKE-FAIL reason=tap tap=/x"
    assert ob.parse_text(text).smoke() is False


def test_tap_forwarding() -> None:
    """TAP 原文逐行还原，且不被误认作标记。"""
    text = "\n".join(
        [
            "[    9.0] smoke.sh[1]: AIRYMAX-TAP|1..2",
            "AIRYMAX-TAP|ok 1 - desktop ready",
            "AIRYMAX-TAP|ok 2 - no failed units",
            "AIRYMAX-SMOKE-PASS tap=/var/log/s.tap",
        ]
    )
    log = ob.parse_text(text)
    assert log.tap == (
        "1..2",
        "ok 1 - desktop ready",
        "ok 2 - no failed units",
    )
    assert log.find("AIRYMAX-TAP") == ()


def test_chain_forwarding() -> None:
    """关键链原文逐行还原，且不产生同名标记。"""
    text = "\n".join(
        [
            "[    9.0] desktop_ready.sh[1]: AIRYMAX-BOOT-CHAIN|graphical.target @7.1s",
            "AIRYMAX-BOOT-CHAIN|└─multi-user.target @7.1s",
            "AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11",
        ]
    )
    log = ob.parse_text(text)
    assert log.chain == (
        "graphical.target @7.1s",
        "└─multi-user.target @7.1s",
    )
    assert log.tap == ()
    assert log.find(ob.TAG_CHAIN) == ()


def test_find_keeps_order() -> None:
    """同标签多次出现按串口顺序返回，末条即 last 结果。"""
    text = "AIRYMAX-DESKTOP-DIAG session first\nAIRYMAX-DESKTOP-DIAG session second"
    log = ob.parse_text(text)
    found = log.find(ob.TAG_DIAG)
    assert len(found) == 2
    assert log.last(ob.TAG_DIAG) is found[-1]


def test_plain_lines_ignored() -> None:
    """内核普通行不产生标记，原始行完整保留。"""
    text = "[    0.000000] Linux version 6.6.0\n[    1.0] systemd[1]: Reached"
    log = ob.parse_text(text)
    assert log.marks == ()
    assert len(log.raw) == 2


def test_fault_uses_env_pattern() -> None:
    """负向证据正则由 verdict.env 提供，命中即返回原始行。"""
    env = {"BOOT_FAIL": "Kernel panic|AIRYMAX-DESKTOP-FAIL|AIRYMAX-SMOKE-FAIL"}
    text = "\n".join(
        [
            "[    3.0] kernel: booting",
            "[    4.0] d.sh[9]: AIRYMAX-DESKTOP-FAIL reason=session path=/x",
            "[    5.0] d.sh[9]: AIRYMAX-SMOKE-PASS tap=/y",
        ]
    )
    log = ob.parse_text(text)
    assert log.fault(env["BOOT_FAIL"]) == (
        "[    4.0] d.sh[9]: AIRYMAX-DESKTOP-FAIL reason=session path=/x"
    )
    assert ob.parse_text(HEALTH).fault(env["BOOT_FAIL"]) is None


def test_load_env_strips_quotes(tmp_path) -> None:
    """单引号包裹的正则原样取出，注释与空行被忽略。"""
    path = tmp_path / "verdict.env"
    path.write_text("# c\n\nBOOT_FAIL='a|b'\nQ='x=y'\n", encoding="utf-8")
    assert ob.load_env(path) == {"BOOT_FAIL": "a|b", "Q": "x=y"}


def test_load_env_rejects_incomplete(tmp_path) -> None:
    """缺少 BOOT_FAIL 视为事实源损坏。"""
    path = tmp_path / "verdict.env"
    path.write_text("OTHER='x'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lacks BOOT_FAIL"):
        ob.load_env(path)


@pytest.mark.skipif(
    not ob.DEFAULT_ENV.is_file(), reason="distro 子模块未检出"
)
def test_default_env_matches_distro() -> None:
    """仓库内 verdict.env 可被直接消费，且含全部负向证据键。"""
    env = ob.load_env()
    assert "AIRYMAX-SMOKE-FAIL" in env["BOOT_FAIL"]
    assert set(env) >= {"BOOT_FAIL", "INST_FAIL", "RPM_FAIL", "PAY_FAIL"}


def test_read_log_handles_crlf(tmp_path) -> None:
    """CRLF 文本同样解析，替换字符不破坏标记识别。"""
    path = tmp_path / "serial.log"
    path.write_bytes(b"AIRYMAX-SMOKE-PASS tap=/x\r\n\xff junk\r\n")
    log = ob.read_log(path)
    assert log.smoke() is True
    assert len(log.raw) == 2
