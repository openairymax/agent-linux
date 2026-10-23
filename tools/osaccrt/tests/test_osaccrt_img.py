# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_img 单元测试：清单文法、逐固件结论与制品摘要。"""

from __future__ import annotations

import hashlib

import pytest

import osaccrt_img as oi

ISO_SHA = "46b24ae1190482e4a8fdc8a59230f921cb6747d35e2bf328cf47624c306ef533"

REAL = f"""# AirymaxOS install test manifest
iso {ISO_SHA} airymaxos-0.0.3-x86_64.iso
arch=x86_64
firmware=both
runs=1
max_boot=auto
accel=kvm
install_timeout=3600
run bios run=1 install=pass
run bios run=1 boot=pass boot_s=13
run uefi run=1 install=pass
run uefi run=1 boot=pass boot_s=14
result=pass
"""


def test_real_manifest() -> None:
    """真机清单被解析为镜像身份与两条固件结论。"""
    img = oi.parse_text(REAL)
    assert img.sha256 == ISO_SHA
    assert img.name == "airymaxos-0.0.3-x86_64.iso"
    assert img.arch == "x86_64"
    assert img.firmware == "both"
    assert img.runs == 1
    assert img.result == "pass"
    assert img.passed is True


def test_run_rows_merge() -> None:
    """同一轮结论跨行给出时按键归并，固件顺序保持稳定。"""
    img = oi.parse_text(REAL)
    assert [row.firmware for row in img.boots] == ["bios", "uefi"]
    assert img.boots_of("bios") == (oi.Boot("bios", 1, "pass", "pass", 13.0),)
    assert img.boots_of("uefi")[0].secs == 14.0
    assert img.boots_of("legacy") == ()


def test_failed_boot_is_not_passed() -> None:
    """任一轮冷启动失败即整体不通过。"""
    text = REAL.replace("boot=pass boot_s=14", "boot=fail")
    img = oi.parse_text(text)
    assert img.boots_of("uefi")[0].passed is False
    assert img.passed is False


def test_missing_iso_rejected() -> None:
    """缺 iso 行视为清单不完整。"""
    text = "arch=x86_64\nfirmware=both\nruns=1\nresult=pass\n"
    with pytest.raises(ValueError, match="lacks the iso line"):
        oi.parse_text(text)


def test_bad_sha_rejected() -> None:
    """摘要长度、大小写或分隔符不合规一律拒绝。"""
    base = "arch=x86_64\nfirmware=both\nruns=1\nresult=pass\n"
    for bad in ("A" * 64, "0" * 63, "0" * 64 + " x.iso extra"):
        with pytest.raises(ValueError, match="iso "):
            oi.parse_text(f"iso {bad}\n{base}")


def test_missing_required_rejected() -> None:
    """缺清单级必填键即拒绝并列出缺失项。"""
    text = f"iso {ISO_SHA} a.iso\narch=x86_64\nfirmware=both\nruns=1\n"
    with pytest.raises(ValueError, match="lacks fields: \\['result'\\]"):
        oi.parse_text(text)


def test_bad_run_row_rejected() -> None:
    """轮次序号非正整数、行内缺等号均拒绝。"""
    head = (
        f"iso {ISO_SHA} a.iso\narch=x86_64\nfirmware=both\n"
        "runs=1\nresult=pass\n"
    )
    with pytest.raises(ValueError, match="run index must be positive"):
        oi.parse_text(head + "run bios run=0 install=pass boot=pass\n")
    with pytest.raises(ValueError, match="not key=value"):
        oi.parse_text(head + "run bios run=1 install=pass boot=pass\njunk\n")


def test_incomplete_run_rejected() -> None:
    """run 行缺 install 或 boot 时拒绝。"""
    head = (
        f"iso {ISO_SHA} a.iso\narch=x86_64\nfirmware=both\n"
        "runs=1\nresult=pass\n"
    )
    with pytest.raises(ValueError, match="lacks fields: \\['boot'\\]"):
        oi.parse_text(head + "run bios run=1 install=pass\n")


def test_digest_matches_hashlib(tmp_path) -> None:
    """分块摘要与小文件一次摘要一致。"""
    path = tmp_path / "a.iso"
    path.write_bytes(b"abc")
    assert oi.digest(path) == hashlib.sha256(b"abc").hexdigest()


def test_read_manifest(tmp_path) -> None:
    """清单文件可直接读取解析。"""
    path = tmp_path / "install-manifest.txt"
    path.write_text(REAL, encoding="utf-8")
    assert oi.read_manifest(path).sha256 == ISO_SHA
