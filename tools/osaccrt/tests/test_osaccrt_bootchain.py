# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_bootchain 单元测试：关键链原文还原与四态结论。

用例取自 ``systemd-analyze critical-chain`` 的真实输出形状：链首是
``graphical.target @<整机耗时>``，其下逐级缩进并以 ``└─`` 引导，只有
非链首单元才带 ``+<自身耗时>``。构造用例覆盖 FAIL、BLOCKED 与证据
缺失三条拒绝路径。
"""

from __future__ import annotations

import json

import pytest

import osaccrt_boot as ob
import osaccrt_bootchain as oc
import osaccrt_model as om
import osaccrt_spec as osp

CHAIN_OK = (
    "graphical.target @7.178s",
    "└─multi-user.target @7.178s",
    "  └─lightdm.service @6.976s +201ms",
    "    └─systemd-user-sessions.service @6.970s +19ms",
)

CHAIN_SLOW = (
    "graphical.target @41.200s",
    "└─multi-user.target @41.200s",
    "  └─lightdm.service @12.300s +6.5s",
)


def _table(limit: str = "< 5") -> osp.SpecTable:
    """单条 boot.service 阈值表，键与来源与 specs/nfr.toml 一致。"""
    return osp.parse_spec({
        "schema": 1,
        "threshold": [{
            "key": "boot.service",
            "source": "NFR-P-005",
            "limit": limit,
            "unit": "s",
        }],
    })


def _log(*lines: str) -> ob.BootLog:
    """把关键链原文包成串口会话，模拟探针的原文转发通道。"""
    text = "\n".join(
        f"[    9.0] desktop_ready.sh[5]: {ob.TAG_CHAIN}|{line}"
        for line in lines
    )
    return ob.parse_text(text)


def test_parse_restores_chain() -> None:
    """缩进与树形符号被剥离，单元名与两种时间逐一还原。"""
    trail = oc.parse("bios", CHAIN_OK, "< 5")
    assert [item.unit for item in trail.links] == [
        "graphical.target",
        "multi-user.target",
        "lightdm.service",
        "systemd-user-sessions.service",
    ]
    assert trail.links[0].dur_s is None
    assert trail.links[2].dur_s == pytest.approx(0.201)
    assert trail.links[3].dur_s == pytest.approx(0.019)


def test_total_and_slow() -> None:
    """整机耗时取链首达成时刻，关键服务取自身耗时最长的一环。"""
    trail = oc.parse("bios", CHAIN_OK, "< 5")
    assert trail.root == "graphical.target"
    assert trail.total_s == pytest.approx(7.178)
    assert trail.slow is not None
    assert trail.slow.unit == "lightdm.service"
    assert trail.slow_s == pytest.approx(0.201)
    assert trail.state is om.Verdict.PASS


def test_slow_unit_exceeds_limit() -> None:
    """关键服务超限即 FAIL，与其他单元的耗时无关。"""
    trail = oc.parse("bios", CHAIN_SLOW, "< 5")
    assert trail.slow_s == pytest.approx(6.5)
    assert trail.state is om.Verdict.FAIL


def test_compound_duration() -> None:
    """systemd 的量词可含空格与毫秒，统一折算为秒。"""
    trail = oc.parse(
        "bios",
        ("graphical.target @2min 3.5s", "└─x.service @1min 30s +1min 30s"),
        "< 5",
    )
    assert trail.total_s == pytest.approx(123.5)
    assert trail.slow_s == pytest.approx(90.0)


def test_no_durations_blocked() -> None:
    """只有链首、无任何单元耗时即 BLOCKED，不当作达标。"""
    trail = oc.parse("bios", ("graphical.target @7.178s",), "< 5")
    assert trail.slow is None
    assert trail.slow_s is None
    assert trail.state is om.Verdict.BLOCKED


def test_absent_chain_blocked() -> None:
    """会话未转发关键链时事实留空，结论为阻塞。"""
    trail = oc.parse("uefi", (), "< 5")
    assert trail.root == ""
    assert trail.total_s is None
    assert trail.state is om.Verdict.BLOCKED


def test_parse_drops_noise() -> None:
    """非单元行（启动汇总、无单元名的残行）不进入链。"""
    lines = (
        "Startup finished in 2.178s (kernel) + 5.000s (userspace) = 7.178s",
        "└─ @7.000s +1s",
        "graphical.target @7.178s",
    )
    trail = oc.parse("bios", lines, "< 5")
    assert [item.unit for item in trail.links] == ["graphical.target"]


def test_judge_reads_forwarded_lines() -> None:
    """两个固件的转发原文各自折叠为一条链，按固件名排序。"""
    chain = oc.judge(
        {"uefi": _log(*CHAIN_OK), "bios": _log(*CHAIN_OK)}, _table()
    )
    assert chain.key == oc.CHAIN_KEY
    assert [item.fw for item in chain.trails] == ["bios", "uefi"]
    assert chain.verdict is om.Verdict.PASS
    assert chain.gaps == ()


def test_judge_blocked_gap() -> None:
    """单固件缺关键链即整体阻塞，缺口按固件名列出。"""
    chain = oc.judge({"bios": _log(*CHAIN_OK), "uefi": _log()}, _table())
    assert chain.verdict is om.Verdict.BLOCKED
    assert chain.gaps == ("uefi",)


def test_judge_fail_dominates() -> None:
    """有固件超限时整体失败，缺口只记录阻塞的那一个。"""
    chain = oc.judge(
        {"bios": _log(*CHAIN_SLOW), "uefi": _log()}, _table()
    )
    assert chain.verdict is om.Verdict.FAIL
    assert chain.gaps == ("uefi",)


def test_judge_requires_threshold() -> None:
    """阈值表缺 boot.service 时直接报错，不带默认门限。"""
    table = osp.parse_spec({
        "schema": 1,
        "threshold": [{
            "key": "boot.cold",
            "source": "NFR-P-005",
            "limit": "< 30",
            "unit": "s",
        }],
    })
    with pytest.raises(KeyError):
        oc.judge({"bios": _log(*CHAIN_OK)}, table)


def test_judge_rejects_empty_sessions() -> None:
    """没有任何固件会话时不得给出结论。"""
    with pytest.raises(ValueError, match="non-empty tuple"):
        oc.judge({}, _table())


@pytest.mark.parametrize("trails", [(), ("bios",)])
def test_chain_rejects_bad_trails(trails: tuple[object, ...]) -> None:
    """判定容器只接受非空 Trail 序列。"""
    with pytest.raises(ValueError):
        oc.Chain(oc.CHAIN_KEY, "< 5", trails)  # type: ignore[arg-type]


def test_payload_records_verdict() -> None:
    """取证产物保留每一环，结论与缺口一并落盘。"""
    chain = oc.judge({"bios": _log(*CHAIN_OK)}, _table())
    data = json.loads(oc.payload(chain))
    assert set(data) == {"key", "limit", "verdict", "gaps", "trails"}
    assert data["verdict"] == "PASS"
    row = data["trails"][0]
    assert row["firmware"] == "bios"
    assert row["root"] == "graphical.target"
    assert row["key_unit"] == "lightdm.service"
    assert row["state"] == "PASS"
    assert row["links"][2] == {
        "unit": "lightdm.service",
        "at_s": 6.976,
        "dur_s": 0.201,
    }


def test_artifact_path() -> None:
    """产物落在证据包的采集目录下，键即文件名。"""
    assert oc.artifact() == "step-03-artifacts/boot.service.json"
    assert oc.artifact("boot.edge") == "step-03-artifacts/boot.edge.json"
