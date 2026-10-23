# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_smoke 单元测试：冷启动事实折叠与四态结论。"""

from __future__ import annotations

import json

import pytest

import osaccrt_boot as ob
import osaccrt_img as oi
import osaccrt_model as om
import osaccrt_smoke as os_
import osaccrt_spec as osp

ISO_SHA = "46b24ae1190482e4a8fdc8a59230f921cb6747d35e2bf328cf47624c306ef533"

MANIFEST = f"""# AirymaxOS install test manifest
iso {ISO_SHA} airymaxos-0.0.3-x86_64.iso
arch=x86_64
firmware=both
runs=1
run bios run=1 install=pass
run bios run=1 boot=pass boot_s=13
run uefi run=1 install=pass
run uefi run=1 boot=pass boot_s=14
result=pass
"""

LOG_PASS = """\
[    5.104237] desktop_ready.sh[563]: AIRYMAX-BOOT-HEALTH failed=0
[    7.178580] desktop_ready.sh[563]: AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11
[    7.190000] desktop_ready.sh[563]: AIRYMAX-SMOKE-PASS total=5 failed=0
"""

LOG_SILENT = """\
[    5.104237] desktop_ready.sh[563]: AIRYMAX-BOOT-HEALTH failed=0
[    7.178580] desktop_ready.sh[563]: AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11
"""

LOG_FAILED = """\
[    7.178580] desktop_ready.sh[563]: AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11
[   12.400000] desktop_ready.sh[563]: AIRYMAX-SMOKE-FAIL total=5 failed=1
"""

LOG_FAULT = """\
[    3.000000] airymax-boot: systemd[1]: Failed to start D-Bus System Message Bus.
[    7.178580] desktop_ready.sh[563]: AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11
[    7.190000] desktop_ready.sh[563]: AIRYMAX-SMOKE-PASS total=5 failed=0
"""

LOG_SLOW = """\
[   31.500000] desktop_ready.sh[563]: AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11
[   31.600000] desktop_ready.sh[563]: AIRYMAX-SMOKE-PASS total=5 failed=0
"""

LOG_UNSTAMPED = """\
desktop_ready.sh[563]: AIRYMAX-DESKTOP-READY unit=lightdm class=greeter type=x11
desktop_ready.sh[563]: AIRYMAX-SMOKE-PASS total=5 failed=0
"""

FAULT_RE = "Failed to start"


def _table(limit: str = "< 30") -> osp.SpecTable:
    """单条 boot.cold 阈值表，键与来源与 specs/nfr.toml 一致。"""
    return osp.parse_spec({
        "schema": 1,
        "threshold": [{
            "key": "boot.cold",
            "source": "NFR-P-005",
            "limit": limit,
            "unit": "s",
        }],
    })


def _logs(**texts: str) -> dict[str, ob.BootLog]:
    """按固件名把串口文本解析为会话。"""
    return {name: ob.parse_text(text) for name, text in texts.items()}


def test_both_firmware_pass() -> None:
    """双固件均上报通过且耗时达标时整体通过。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_PASS, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.verdict is om.Verdict.PASS
    assert smoke.limit == "< 30"
    assert [item.fw for item in smoke.samples] == ["bios", "uefi"]
    assert smoke.samples[0].ready_s == pytest.approx(7.17858)
    assert smoke.samples[0].boot_s == 13.0
    assert smoke.gaps == ()


def test_silent_session_blocks() -> None:
    """未上报冒烟标记等于没测到，判 BLOCKED 而不是通过。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_SILENT, uefi=LOG_SILENT),
        _table(),
        FAULT_RE,
    )
    assert smoke.verdict is om.Verdict.BLOCKED
    assert smoke.gaps == ("bios", "uefi")


def test_reported_failure_fails() -> None:
    """会话自报失败即判 FAIL，即便耗时达标。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_PASS, uefi=LOG_FAILED),
        _table(),
        FAULT_RE,
    )
    assert smoke.verdict is om.Verdict.FAIL


def test_fault_line_fails() -> None:
    """负向证据行优先于通过标记。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_FAULT, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.verdict is om.Verdict.FAIL
    assert "Failed to start" in smoke.samples[0].fault


def test_over_limit_fails() -> None:
    """就绪耗时超出阈值即判 FAIL。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_SLOW, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.verdict is om.Verdict.FAIL
    assert smoke.samples[0].secs == pytest.approx(31.5)


def test_manifest_secs_fallback() -> None:
    """串口缺时间戳时退回清单实测秒数，仍可判定。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_UNSTAMPED, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.samples[0].ready_s is None
    assert smoke.samples[0].secs == 13.0
    assert smoke.verdict is om.Verdict.PASS


def test_no_timing_blocks() -> None:
    """串口与清单都没有耗时时判 BLOCKED。"""
    text = MANIFEST.replace("boot=pass boot_s=13", "boot=pass")
    smoke = os_.judge(
        oi.parse_text(text),
        _logs(bios=LOG_UNSTAMPED, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.samples[0].secs is None
    assert smoke.verdict is om.Verdict.BLOCKED
    assert smoke.gaps == ("bios",)


def test_install_failure_fails() -> None:
    """清单装机失败即 FAIL，不被串口通过标记掩盖。"""
    text = MANIFEST.replace("run uefi run=1 install=pass", "run uefi run=1 install=fail")
    smoke = os_.judge(
        oi.parse_text(text),
        _logs(bios=LOG_PASS, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.samples[1].booted is False
    assert smoke.verdict is om.Verdict.FAIL


def test_missing_session_blocks() -> None:
    """缺某个固件的串口会话时判 BLOCKED。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.verdict is om.Verdict.BLOCKED
    assert smoke.gaps == ("uefi",)


def test_limit_comes_from_table() -> None:
    """阈值完全取自阈值表，本模块不自带数字。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_UNSTAMPED, uefi=LOG_UNSTAMPED),
        _table("< 10"),
        FAULT_RE,
    )
    assert smoke.limit == "< 10"
    assert smoke.verdict is om.Verdict.FAIL


def test_worst_boot_wins() -> None:
    """多轮实测取最差一次作为清单耗时。"""
    text = MANIFEST.replace(
        "run bios run=1 boot=pass boot_s=13",
        "run bios run=1 boot=pass boot_s=13\n"
        "run bios run=2 install=pass\n"
        "run bios run=2 boot=pass boot_s=21",
    ).replace("runs=1", "runs=2")
    smoke = os_.judge(
        oi.parse_text(text),
        _logs(bios=LOG_SILENT, uefi=LOG_PASS),
        _table(),
        FAULT_RE,
    )
    assert smoke.samples[0].boot_s == 21.0


def test_unknown_key_rejected() -> None:
    """阈值表缺 boot.cold 时拒绝判定，不静默降级。"""
    table = osp.parse_spec({
        "schema": 1,
        "threshold": [{
            "key": "boot.edge",
            "source": "NFR-P-005",
            "limit": "< 10",
            "unit": "s",
        }],
    })
    with pytest.raises(KeyError):
        os_.judge(
            oi.parse_text(MANIFEST),
            _logs(bios=LOG_PASS, uefi=LOG_PASS),
            table,
            FAULT_RE,
        )


def test_empty_samples_rejected() -> None:
    """没有任何固件事实时不允许构造判定对象。"""
    with pytest.raises(ValueError):
        os_.Smoke(os_.COLD_KEY, "< 30", ())


def test_payload_roundtrip() -> None:
    """取证产物字段完整、可解析且结论一致。"""
    smoke = os_.judge(
        oi.parse_text(MANIFEST),
        _logs(bios=LOG_PASS, uefi=LOG_SILENT),
        _table(),
        FAULT_RE,
    )
    data = json.loads(os_.payload(smoke).decode("utf-8"))
    assert data["key"] == "boot.cold"
    assert data["limit"] == "< 30"
    assert data["verdict"] == "BLOCKED"
    assert data["gaps"] == ["uefi"]
    assert [row["firmware"] for row in data["samples"]] == ["bios", "uefi"]
    assert data["samples"][0]["ready_s"] == pytest.approx(7.17858)
    assert data["samples"][1]["state"] == "BLOCKED"


def test_artifact_path() -> None:
    """产物落在采集目录下，按阈值键命名。"""
    assert os_.artifact() == "step-03-artifacts/boot.cold.json"
    assert os_.artifact("boot.edge") == "step-03-artifacts/boot.edge.json"
