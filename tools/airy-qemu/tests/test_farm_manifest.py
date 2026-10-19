# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_manifest 单元测试：真实清单加载与严格 schema 拒绝路径。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import farm_manifest as fm
import farm_types as ft


def raw_manifest() -> dict[str, Any]:
    """最小合法清单（每个用例独立构造，避免共享可变状态）。"""
    return {
        "version": "1.0.0",
        "machines": [
            {
                "arch": "x86_64",
                "machine": "q35",
                "cpu": "max",
                "console": "ttyS0",
            }
        ],
        "suites": [{"name": "seccomp", "timeout_s": 120}],
    }


def parse(raw: object) -> ft.FarmManifest:
    """以默认来源解析原始数据。"""
    return fm.parse_manifest(raw)


def parse_error(raw: object) -> str:
    """解析并返回错误消息。"""
    with pytest.raises(ft.FarmConfigError) as excinfo:
        parse(raw)
    return excinfo.value.message


def test_real_manifest_machines(farm_dir: Path) -> None:
    manifest = fm.load_manifest(farm_dir / "manifest.yaml")
    assert manifest.version == ft.FARM_VERSION
    assert [machine.arch for machine in manifest.machines] == list(ft.Arch)
    assert manifest.machine_for(ft.Arch.ARM64).console == "ttyAMA0"


def test_real_manifest_suites(farm_dir: Path) -> None:
    manifest = fm.load_manifest(farm_dir / "manifest.yaml")
    by_name = {suite.name: suite.timeout_s for suite in manifest.suites}
    assert by_name == {
        "seccomp": 120,
        "pidfd": ft.DEFAULT_SUITE_TIMEOUT_S,
        "membarrier": ft.DEFAULT_SUITE_TIMEOUT_S,
        "rseq": ft.DEFAULT_SUITE_TIMEOUT_S,
    }


def test_real_manifest_limits(farm_dir: Path) -> None:
    manifest = fm.load_manifest(farm_dir / "manifest.yaml")
    assert manifest.limits == ft.FarmLimits(max_jobs=2, mem_mb=8192, cpus=4)


def test_parse_minimal_manifest() -> None:
    manifest = parse(raw_manifest())
    assert manifest.version == "1.0.0"
    assert manifest.suites[0].timeout_s == 120


def test_parse_default_limits() -> None:
    manifest = parse(raw_manifest())
    assert manifest.limits == ft.FarmLimits()


def test_parse_default_suite_timeout() -> None:
    raw = raw_manifest()
    raw["suites"] = [{"name": "rseq"}]
    assert parse(raw).suites[0].timeout_s == ft.DEFAULT_SUITE_TIMEOUT_S


def test_parse_binary_override() -> None:
    raw = raw_manifest()
    raw["machines"][0]["binary"] = "/opt/qemu/qemu-system-x86_64"
    assert parse(raw).machines[0].qemu_bin.startswith("/opt/qemu/")


def test_parse_limits_override() -> None:
    raw = raw_manifest()
    raw["limits"] = {"max_jobs": 1, "mem_mb": 2048, "cpus": 2}
    assert parse(raw).limits == ft.FarmLimits(1, 2048, 2)


def test_parse_accepts_same_major_version() -> None:
    raw = raw_manifest()
    raw["version"] = "1.9.9"
    assert parse(raw).version == "1.9.9"


@pytest.mark.parametrize("raw", [[], "manifest", 42])
def test_parse_rejects_non_mapping(raw: object) -> None:
    assert "must be a mapping" in parse_error(raw)


def test_parse_rejects_unknown_top_key() -> None:
    raw = raw_manifest()
    raw["machine"] = []
    assert "unknown keys ['machine']" in parse_error(raw)


@pytest.mark.parametrize("key", ["version", "machines", "suites"])
def test_parse_rejects_missing_top_key(key: str) -> None:
    raw = raw_manifest()
    del raw[key]
    assert f"missing keys ['{key}']" in parse_error(raw)


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "", 1])
def test_parse_rejects_bad_version(version: object) -> None:
    raw = raw_manifest()
    raw["version"] = version
    assert "version" in parse_error(raw)


def test_parse_rejects_unsupported_major() -> None:
    raw = raw_manifest()
    raw["version"] = "2.0.0"
    assert "not supported" in parse_error(raw)


@pytest.mark.parametrize("key", ["machines", "suites"])
def test_parse_rejects_empty_sections(key: str) -> None:
    raw = raw_manifest()
    raw[key] = []
    assert f"manifest.{key} must be a non-empty list" in parse_error(raw)


def test_parse_rejects_machine_non_mapping() -> None:
    raw = raw_manifest()
    raw["machines"] = ["q35"]
    assert "machines[0] must be a mapping" in parse_error(raw)


@pytest.mark.parametrize("key", ["arch", "machine", "cpu", "console"])
def test_parse_rejects_machine_missing_key(key: str) -> None:
    raw = raw_manifest()
    del raw["machines"][0][key]
    assert f"missing keys ['{key}']" in parse_error(raw)


def test_parse_rejects_machine_unknown_key() -> None:
    raw = raw_manifest()
    raw["machines"][0]["accel"] = "kvm"
    assert "unknown keys ['accel']" in parse_error(raw)


def test_parse_rejects_unsupported_arch() -> None:
    raw = raw_manifest()
    raw["machines"][0]["arch"] = "s390x"
    assert "unsupported arch 's390x'" in parse_error(raw)


def test_parse_rejects_duplicate_arch() -> None:
    raw = raw_manifest()
    raw["machines"].append(dict(raw["machines"][0]))
    assert "duplicate machine arches" in parse_error(raw)


def test_parse_rejects_bad_console() -> None:
    raw = raw_manifest()
    raw["machines"][0]["console"] = "tty S0"
    assert "console" in parse_error(raw)


def test_parse_rejects_non_string_binary() -> None:
    raw = raw_manifest()
    raw["machines"][0]["binary"] = 7
    assert "binary must be a string" in parse_error(raw)


def test_parse_rejects_suite_non_mapping() -> None:
    raw = raw_manifest()
    raw["suites"] = ["seccomp"]
    assert "suites[0] must be a mapping" in parse_error(raw)


def test_parse_rejects_suite_missing_name() -> None:
    raw = raw_manifest()
    raw["suites"] = [{"timeout_s": 60}]
    assert "missing keys ['name']" in parse_error(raw)


def test_parse_rejects_suite_unknown_key() -> None:
    raw = raw_manifest()
    raw["suites"][0]["target"] = "seccomp"
    assert "unknown keys ['target']" in parse_error(raw)


def test_parse_rejects_duplicate_suite() -> None:
    raw = raw_manifest()
    raw["suites"].append({"name": "seccomp"})
    assert "duplicate suite names" in parse_error(raw)


@pytest.mark.parametrize("timeout", [0, -1, True, "60"])
def test_parse_rejects_bad_timeout(timeout: object) -> None:
    raw = raw_manifest()
    raw["suites"][0]["timeout_s"] = timeout
    assert "timeout_s must be a positive integer" in parse_error(raw)


def test_parse_rejects_limits_non_mapping() -> None:
    raw = raw_manifest()
    raw["limits"] = []
    assert "manifest.limits must be a mapping" in parse_error(raw)


def test_parse_rejects_limits_unknown_key() -> None:
    raw = raw_manifest()
    raw["limits"] = {"jobs": 2}
    assert "unknown keys ['jobs']" in parse_error(raw)


@pytest.mark.parametrize("key", ["max_jobs", "mem_mb", "cpus"])
@pytest.mark.parametrize("value", [0, -4, True])
def test_parse_rejects_bad_limit(key: str, value: object) -> None:
    raw = raw_manifest()
    raw["limits"] = {key: value}
    assert f"{key} must be a positive integer" in parse_error(raw)


def test_load_manifest_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.yaml"
    with pytest.raises(ft.FarmConfigError) as excinfo:
        fm.load_manifest(missing)
    assert "cannot read manifest" in excinfo.value.message


def test_load_manifest_invalid_yaml(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: [1.0.0\n", encoding="utf-8")
    with pytest.raises(ft.FarmConfigError) as excinfo:
        fm.load_manifest(broken)
    assert "invalid YAML" in excinfo.value.message


def test_load_manifest_reports_source(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: '1.0.0'\n", encoding="utf-8")
    with pytest.raises(ft.FarmConfigError) as excinfo:
        fm.load_manifest(str(bad))
    assert str(bad) in excinfo.value.message


def test_parse_error_code_is_config() -> None:
    with pytest.raises(ft.FarmConfigError) as excinfo:
        parse({})
    assert excinfo.value.code == ft.AIRY_QEMU_CONFIG_ERROR
