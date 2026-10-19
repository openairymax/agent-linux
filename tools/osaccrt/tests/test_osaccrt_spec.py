# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_spec 单元测试：阈值表加载、结构与原文一致性。

默认表逐条对照 NFR 原文数值；构造用例覆盖 schema 版本、字段集合、
键命名、来源编号、比较表达式与键唯一性等拒绝路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import osaccrt_spec as osp

EXPECTED = {
    "NFR-P-001": (
        ("sched.p99", "< 100", "ms"),
        ("sched.p999", "< 200", "ms"),
        ("sched.rt", "< 10", "ms"),
    ),
    "NFR-P-002": (
        ("ipc.throughput", "> 100000", "msg/s"),
        ("ipc.latency", "< 10", "us"),
    ),
    "NFR-P-004": (
        ("token.kv_hit", "> 90", "%"),
        ("token.batch", "> 85", "%"),
    ),
    "NFR-P-005": (
        ("boot.cold", "< 30", "s"),
        ("boot.edge", "< 10", "s"),
        ("boot.service", "< 5", "s"),
    ),
    "NFR-P-006": (
        ("mem.total", "< 512", "MB"),
        ("mem.edge", "< 256", "MB"),
    ),
    "NFR-S-001": (("sec.capability", "= 100", "%"),),
    "NFR-R-001": (
        ("soak.fault", "= 0", "count"),
        ("soak.leak", "= 0", "count"),
        ("soak.mttr", "< 5", "min"),
    ),
}


def base(**over: object) -> dict[str, object]:
    """构造单条阈值的合法表，按需覆盖字段。"""
    row: dict[str, object] = {
        "key": "boot.cold",
        "source": "NFR-P-005",
        "limit": "< 30",
        "unit": "s",
    }
    row.update(over)
    return {"schema": 1, "threshold": [row]}


def test_default_matches_nfr() -> None:
    """默认表与 NFR 原文逐条一致，且只收录实验矩阵引用的来源。"""
    table = osp.load_spec()
    sources = {item.source for item in table.thresholds}
    assert sources == set(EXPECTED)
    for source, rows in EXPECTED.items():
        got = tuple(
            (item.key, item.limit, item.unit)
            for item in table.for_source(source)
        )
        assert got == rows


def test_default_is_unique() -> None:
    """默认表键唯一且数量与矩阵一致。"""
    table = osp.load_spec()
    assert table.schema == osp.SPEC_SCHEMA
    assert len(table.keys) == sum(len(rows) for rows in EXPECTED.values())
    assert len(set(table.keys)) == len(table.keys)


def test_get_returns_threshold() -> None:
    """按键取出的条目保留原文限制与单位。"""
    item = osp.load_spec().get("boot.cold")
    assert (item.source, item.limit, item.unit) == ("NFR-P-005", "< 30", "s")


def test_get_unknown_key() -> None:
    """未知键直接报错，不返回默认值。"""
    with pytest.raises(KeyError):
        osp.load_spec().get("boot.missing")


def test_missing_file(tmp_path: Path) -> None:
    """文件缺失带路径报错。"""
    with pytest.raises(ValueError, match="cannot read spec"):
        osp.load_spec(tmp_path / "absent.toml")


def test_bad_toml(tmp_path: Path) -> None:
    """TOML 语法错误被拒绝。"""
    target = tmp_path / "bad.toml"
    target.write_text("schema = ?\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid spec"):
        osp.load_spec(target)


def test_osacc_source_accepted() -> None:
    """OS-ACC 编号与 NFR 编号同样合法。"""
    table = osp.parse_spec(base(source="OS-ACC-BOOT-010"))
    assert table.get("boot.cold").source == "OS-ACC-BOOT-010"


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([], "spec root must be a table"),
        ({"schema": 1}, "missing fields"),
        ({"schema": 1, "threshold": [], "extra": 1}, "unknown fields"),
        ({"schema": True, "threshold": []}, "schema must be an integer"),
        ({"schema": 2, "threshold": []}, "unsupported schema"),
        ({"schema": 1, "threshold": {}}, "array of tables"),
        ({"schema": 1, "threshold": ["x"]}, r"threshold\[0\] must be a table"),
        (base(extra="x"), r"threshold\[0\] has unknown fields"),
        (base(key="Boot"), r"threshold\[0\]\.key is malformed"),
        (base(key="boot/cold"), r"threshold\[0\]\.key is malformed"),
        (base(source="NFR-X-1"), r"threshold\[0\]\.source is malformed"),
        (base(limit="30"), r"threshold\[0\]\.limit is malformed"),
        (base(limit="~30"), r"threshold\[0\]\.limit is malformed"),
        (base(unit=30), r"threshold\[0\]\.unit must be a non-empty string"),
    ],
)
def test_reject_malformed(data: object, match: str) -> None:
    """结构或取值非法时整表拒绝，并带定位信息。"""
    with pytest.raises(ValueError, match=match):
        osp.parse_spec(data)  # type: ignore[arg-type]


def test_reject_missing_field() -> None:
    """缺字段的表被拒绝。"""
    data = base()
    rows = data["threshold"]
    assert isinstance(rows, list)
    del rows[0]["unit"]
    with pytest.raises(ValueError, match=r"threshold\[0\] is missing fields"):
        osp.parse_spec(data)


def test_reject_duplicate_key() -> None:
    """重复键在整表层面拒绝。"""
    data = base()
    rows = data["threshold"]
    assert isinstance(rows, list)
    rows.append(dict(rows[0]))
    with pytest.raises(ValueError, match="duplicate threshold keys"):
        osp.parse_spec(data)


def test_reject_empty_table() -> None:
    """空表不得作为阈值来源。"""
    with pytest.raises(ValueError, match="non-empty tuple"):
        osp.parse_spec({"schema": 1, "threshold": []})
