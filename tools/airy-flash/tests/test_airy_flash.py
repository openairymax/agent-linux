# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/airy-flash 单元与端到端用例。

全部用例不要求 root、不触碰真实块设备：块设备分支用临时 sysfs 树 + 假
stat 模式走通，写入/回读用普通文件当靶子（--allow-file 的测试用途）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import airy_flash as af
from flash_types import (
    EXIT_CHECK,
    EXIT_IO,
    EXIT_OK,
    EXIT_USAGE,
    Check,
    DeviceFacts,
    FlashError,
    ImageFacts,
    Mount,
)


# ─── 夹具 ───────────────────────────────────────────────────────────────────


def make_sysfs(
    root: Path,
    *,
    disks: tuple[str, ...] = ("sdb",),
    partitions: tuple[tuple[str, str], ...] = (("sdb", "sdb1"),),
    sectors: int = 2048000,
) -> Path:
    """搭一棵最小 sysfs：<root>/sys/class/block/<name> → <root>/devices/.../block/<name>。"""
    sys_block = root / "sys" / "class" / "block"
    sys_block.mkdir(parents=True)
    devices = root / "devices" / "block"

    for disk in disks:
        real = devices / disk
        real.mkdir(parents=True)
        (real / "size").write_text(f"{sectors}\n", encoding="utf-8")
        (real / "removable").write_text("1\n", encoding="utf-8")
        (real / "ro").write_text("0\n", encoding="utf-8")
        (real / "device").mkdir()
        (real / "device" / "model").write_text("Airy USB 3.0\n", encoding="utf-8")
        (real / "device" / "serial").write_text("DEADBEEF\n", encoding="utf-8")
        (sys_block / disk).symlink_to(real)

    for disk, part in partitions:
        real = devices / disk / part
        real.mkdir()
        (real / "partition").write_text("1\n", encoding="utf-8")
        (sys_block / part).symlink_to(real)

    return sys_block


class FakeMode:
    """把 stat.S_ISBLK/S_ISREG 替成常量判定，免去 root 下 mknod 的需求。"""

    def __init__(self, *, block: bool) -> None:
        self.block = block

    def S_ISBLK(self, _mode: int) -> bool:  # noqa: N802 - 对齐 stat 模块命名
        return self.block

    def S_ISREG(self, _mode: int) -> bool:  # noqa: N802
        return not self.block


def make_image(tmp_path: Path, payload: bytes = b"AirymaxOS" * 512) -> tuple[Path, str]:
    """造一个镜像与配套 .sha256 侧车文件。"""
    image = tmp_path / "AirymaxOS-0.0.3-x86_64.iso"
    image.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    image.with_suffix(image.suffix + ".sha256").write_text(
        f"{digest}  {image.name}\n", encoding="utf-8"
    )
    return image, digest


def make_target(tmp_path: Path, size: int) -> Path:
    target = tmp_path / "usb.img"
    target.write_bytes(b"\0" * size)
    return target


# ─── 人读格式化 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expect"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.00 KiB"),
        (3 * 1024**3, "3.00 GiB"),
        (3680174080, "3.43 GiB"),
    ],
)
def test_human_bytes(value: int, expect: str) -> None:
    assert af.human_bytes(value) == expect


@pytest.mark.parametrize(
    ("value", "expect"),
    [(0, "0s"), (42, "42s"), (66, "1m06s"), (3720, "1h02m")],
)
def test_human_duration(value: int, expect: str) -> None:
    assert af.human_duration(value) == expect


def test_cjk_padding_is_display_width_aware() -> None:
    assert af.display_width("预检") == 4
    assert af.display_width("校验镜像") == 8
    assert af.pad("预检", 6) == "预检  "
    assert af.pad("abc", 6) == "abc   "
    # pad 后右侧仍按两列对齐
    assert af.display_width(af.pad("校验镜像", 10)) == 10


def test_short_digest_keeps_short_values_whole() -> None:
    digest = "0123456789abcdef" * 4
    assert af.short_digest(digest) == "01234567…89abcdef"
    assert af.short_digest("deadbeef") == "deadbeef"


# ─── 校验和 ─────────────────────────────────────────────────────────────────


def test_parse_sums_accepts_binary_marker_and_dot_prefix() -> None:
    digest = "a" * 64
    other = "b" * 64
    text = "\n".join(
        [
            "# sha256sum 生成",
            f"{digest}  AirymaxOS-0.0.3-x86_64.iso",
            f"{other} *./nested/rebuild.iso",
            "",
            "not-a-digest  skip.iso",
            "lonely-token",
        ]
    )
    table = af.parse_sums(text)
    assert table == {
        "AirymaxOS-0.0.3-x86_64.iso": digest,
        "rebuild.iso": other,
    }


def test_resolve_expected_prefers_explicit_sha256(tmp_path: Path) -> None:
    image, digest = make_image(tmp_path)
    expected, source = af.resolve_expected(image, sha256=digest.upper())
    assert expected == digest
    assert source == "--sha256"


def test_resolve_expected_rejects_malformed_sha256(tmp_path: Path) -> None:
    image, _ = make_image(tmp_path)
    with pytest.raises(FlashError) as excinfo:
        af.resolve_expected(image, sha256="xyz")
    assert excinfo.value.code == EXIT_USAGE


def test_resolve_expected_reads_sidecar(tmp_path: Path) -> None:
    image, digest = make_image(tmp_path)
    expected, source = af.resolve_expected(image)
    assert expected == digest
    assert source.endswith(".iso.sha256")


def test_resolve_expected_reads_sums_file(tmp_path: Path) -> None:
    image, digest = make_image(tmp_path)
    image.with_suffix(image.suffix + ".sha256").unlink()
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"{digest}  {image.name}\n", encoding="utf-8")
    expected, source = af.resolve_expected(image)
    assert (expected, source) == (digest, str(sums))


def test_resolve_expected_single_entry_sums_file_is_used(tmp_path: Path) -> None:
    image, digest = make_image(tmp_path)
    sums = tmp_path / "peer.sha256"
    sums.write_text(f"{digest}  other-name.iso\n", encoding="utf-8")
    expected, source = af.resolve_expected(image, sums=str(sums))
    assert (expected, source) == (digest, str(sums))


def test_resolve_expected_multi_entry_sums_without_match_fails(tmp_path: Path) -> None:
    image, _ = make_image(tmp_path)
    sums = tmp_path / "peer.sha256"
    sums.write_text(f"{'a' * 64}  x.iso\n{'b' * 64}  y.iso\n", encoding="utf-8")
    with pytest.raises(FlashError) as excinfo:
        af.resolve_expected(image, sums=str(sums))
    assert excinfo.value.code == EXIT_CHECK


def test_resolve_expected_without_any_source_returns_empty(tmp_path: Path) -> None:
    image = tmp_path / "plain.iso"
    image.write_bytes(b"x")
    assert af.resolve_expected(image) == ("", "")


def test_sha256_file_reports_full_progress(tmp_path: Path) -> None:
    image = tmp_path / "x.iso"
    image.write_bytes(b"0123456789" * 100)
    seen: list[tuple[int, int]] = []
    digest = af.sha256_file(image, chunk=100, progress=lambda d, t: seen.append((d, t)))
    assert digest == hashlib.sha256(image.read_bytes()).hexdigest()
    assert seen[-1] == (1000, 1000)
    assert all(total == 1000 for _, total in seen)


# ─── 设备探测 ───────────────────────────────────────────────────────────────


def test_read_mounts_parses_and_tolerates_missing(tmp_path: Path) -> None:
    table = tmp_path / "mounts"
    table.write_text(
        "/dev/sdb1 /boot/efi vfat rw 0 0\n"
        "proc /proc proc rw 0 0\n"
        "broken-line\n",
        encoding="utf-8",
    )
    mounts = af.read_mounts(table)
    assert mounts == [Mount("/dev/sdb1", "/boot/efi"), Mount("proc", "/proc")]
    assert af.read_mounts(tmp_path / "absent") == []


def test_system_disks_resolves_partition_to_parent(tmp_path: Path) -> None:
    sys_block = make_sysfs(tmp_path)
    mounts = [
        Mount("/dev/sdb1", "/"),
        Mount("/dev/sdb1", "/boot"),
        Mount("/dev/sdc1", "/data"),
        Mount("tmpfs", "/run"),
    ]
    assert af.system_disks(mounts, sys_block) == ("sdb",)


def test_parent_disk_strips_partition_suffix(tmp_path: Path) -> None:
    sys_block = make_sysfs(
        tmp_path, disks=("nvme0n1",), partitions=(("nvme0n1", "nvme0n1p2"),)
    )
    assert af._parent_disk("nvme0n1p2", sys_block) == "nvme0n1"
    assert af._parent_disk("nvme0n1", sys_block) == "nvme0n1"


def test_probe_image_facts_and_errors(tmp_path: Path) -> None:
    image, _ = make_image(tmp_path, b"payload")
    facts = af.probe_image(image)
    assert facts.size == 7
    assert facts.path == Path(str(image))

    with pytest.raises(FlashError) as missing:
        af.probe_image(tmp_path / "absent.iso")
    assert missing.value.code == EXIT_USAGE

    with pytest.raises(FlashError) as directory:
        af.probe_image(tmp_path)
    assert directory.value.code == EXIT_USAGE

    empty = tmp_path / "empty.iso"
    empty.write_bytes(b"")
    with pytest.raises(FlashError) as blank:
        af.probe_image(empty)
    assert blank.value.code == EXIT_CHECK


def test_probe_device_file_requires_allow_file(tmp_path: Path) -> None:
    target = make_target(tmp_path, 4096)
    with pytest.raises(FlashError) as excinfo:
        af.probe_device(target, mounts=[])
    assert excinfo.value.code == EXIT_CHECK

    facts = af.probe_device(target, mounts=[], allow_file=True)
    assert facts.kind == "file"
    assert facts.size == 4096
    assert "普通文件" in facts.note
    assert not facts.is_system_disk


def test_probe_device_missing_target(tmp_path: Path) -> None:
    with pytest.raises(FlashError) as excinfo:
        af.probe_device(tmp_path / "absent", mounts=[], allow_file=True)
    assert excinfo.value.code == EXIT_CHECK


def test_probe_device_block_reads_sysfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys_block = make_sysfs(tmp_path)
    node = tmp_path / "sdb"
    node.write_bytes(b"")
    monkeypatch.setattr(af, "stat", FakeMode(block=True))

    facts = af.probe_device(node, mounts=[], sys_block=sys_block)
    assert facts.kind == "block"
    assert facts.size == 2048000 * 512
    assert facts.model == "Airy USB 3.0"
    assert facts.serial == "DEADBEEF"
    assert facts.removable is True
    assert facts.read_only is False
    assert facts.mountpoints == ()
    assert facts.system_disks == ()
    assert facts.is_system_disk is False


def test_probe_device_block_collects_mountpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys_block = make_sysfs(tmp_path)
    node = tmp_path / "sdb"
    node.write_bytes(b"")
    monkeypatch.setattr(af, "stat", FakeMode(block=True))

    facts = af.probe_device(
        node, mounts=[Mount(f"{node}1", "/media/usb")], sys_block=sys_block
    )
    assert facts.mountpoints == ("/media/usb",)


# ─── 预检 ───────────────────────────────────────────────────────────────────


def _image(size: int = 4096, expected: str = "") -> ImageFacts:
    return ImageFacts(path=Path("x.iso"), size=size, expected=expected)


def _device(**kwargs: object) -> DeviceFacts:
    base: dict[str, object] = {
        "path": Path("/dev/sdb"),
        "kind": "block",
        "size": 8192,
        "system_disks": (),
    }
    base.update(kwargs)
    return DeviceFacts(**base)  # type: ignore[arg-type]


def test_preflight_passes_on_clean_device() -> None:
    checks = af.preflight(
        _image(), _device(), expected="a" * 64, source="x.iso.sha256"
    )
    assert all(c.ok for c in checks)
    assert not any(c.skipped for c in checks)


def test_preflight_rejects_system_disk() -> None:
    checks = af.preflight(
        _image(),
        _device(system_disks=("sdb",)),
        expected="a" * 64,
        source="x.iso.sha256",
    )
    failed = [c.name for c in checks if not c.ok]
    assert failed == ["非系统盘"]


def test_preflight_rejects_mounted_and_read_only_and_small() -> None:
    checks = af.preflight(
        _image(65536),
        _device(mountpoints=("/media/usb",), read_only=True),
        expected="a" * 64,
        source="x.iso.sha256",
    )
    failed = {c.name for c in checks if not c.ok}
    assert failed == {"未挂载", "非只读", "容量充足"}


def test_preflight_requires_digest_unless_allowed() -> None:
    strict = af.preflight(_image(), _device(), expected="", source="")
    assert [c.name for c in strict if not c.ok] == ["校验和来源"]

    relaxed = af.preflight(
        _image(), _device(), expected="", source="", allow_unverified=True
    )
    skipped = [c for c in relaxed if c.skipped]
    assert [c.name for c in skipped] == ["校验和来源"]


def test_preflight_marks_file_target_as_skipped_not_failed() -> None:
    checks = af.preflight(
        _image(),
        _device(path=Path("/tmp/usb.img"), kind="file"),
        expected="a" * 64,
        source="x.iso.sha256",
    )
    assert [c.name for c in checks if c.skipped] == ["目标是块设备", "非只读"]
    assert all(c.ok for c in checks)


# ─── 写入与回读 ─────────────────────────────────────────────────────────────


def test_sample_ranges_small_image_is_whole() -> None:
    assert af.sample_ranges(48, window=16) == [(0, 48)]
    assert af.sample_ranges(47, window=16) == [(0, 47)]


def test_sample_ranges_large_image_is_head_middle_tail() -> None:
    size = 1000
    ranges = af.sample_ranges(size, window=16)
    assert len(ranges) == 3
    assert ranges[0] == (0, 16)
    assert ranges[2] == (size - 16, size)
    middle_start, middle_end = ranges[1]
    assert middle_end - middle_start == 16
    assert abs((middle_start + middle_end) // 2 - size // 2) <= 1
    assert all(0 <= start < end <= size for start, end in ranges)


def test_write_image_copies_and_reports_progress(tmp_path: Path) -> None:
    image = tmp_path / "x.iso"
    image.write_bytes(b"payload" * 1000)
    target = make_target(tmp_path, 8192)
    seen: list[tuple[int, int]] = []

    written = af.write_image(
        image,
        target,
        chunk=1024,
        progress=lambda d, t: seen.append((d, t)),
        file_target=True,
    )
    assert written == 7000
    assert target.stat().st_size == 7000
    assert target.read_bytes() == image.read_bytes()
    assert seen[-1] == (7000, 7000)


def test_flush_device_syncs_file_target(tmp_path: Path) -> None:
    af.flush_device(make_target(tmp_path, 512))


def test_readback_full_matches_and_detects_tamper(tmp_path: Path) -> None:
    image = tmp_path / "x.iso"
    image.write_bytes(b"0123456789" * 100)
    good = tmp_path / "good.img"
    good.write_bytes(image.read_bytes())

    same, detail, ranges = af.readback(image, good, "full")
    assert same is True
    assert ranges == [(0, 1000)]
    assert "1000" in detail or "1000 B" in detail

    bad = tmp_path / "bad.img"
    payload = bytearray(image.read_bytes())
    payload[42] ^= 0xFF
    bad.write_bytes(bytes(payload))
    same, detail, _ = af.readback(image, bad, "full")
    assert same is False
    assert "偏移 42" in detail


def test_readback_sample_mode_uses_windows(tmp_path: Path) -> None:
    image = tmp_path / "x.iso"
    image.write_bytes(b"\x5a" * 200)
    target = tmp_path / "t.img"
    target.write_bytes(image.read_bytes())
    same, detail, ranges = af.readback(image, target, "sample", chunk=32, window=16)
    assert same is True
    assert len(ranges) == 3
    assert "3 段" in detail


# ─── CLI ────────────────────────────────────────────────────────────────────


def test_cli_plan_does_not_touch_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    image, digest = make_image(tmp_path)
    target = make_target(tmp_path, 16384)
    before = target.read_bytes()

    code = af.main(
        [
            "plan",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--no-progress",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert target.read_bytes() == before
    assert f"AIRY-FLASH-PASS" in out
    assert digest[:8] in out


def test_cli_write_then_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    image, _ = make_image(tmp_path)
    target = make_target(tmp_path, 32768)
    report_path = tmp_path / "reports" / "flash.json"

    code = af.main(
        [
            "write",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--no-progress",
            "--report",
            str(report_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "阶段" in out and "下一步" in out
    assert target.read_bytes() == image.read_bytes()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["tool"] == "airy-flash"
    assert report["result"] == "pass"
    assert report["command"] == "write"
    assert report["image"]["sha256"] == report["image"]["sha256_expected"]
    assert [s["name"] for s in report["stages"]] == [
        "校验镜像",
        "写入设备",
        "刷盘同步",
        "回读校验",
    ]
    assert all(s["status"] == "ok" for s in report["stages"])

    code = af.main(
        [
            "verify",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--no-progress",
        ]
    )
    assert code == EXIT_OK
    assert "AIRY-FLASH-PASS" in capsys.readouterr().out


def test_cli_verify_detects_tampered_device(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image, _ = make_image(tmp_path)
    target = make_target(tmp_path, 8192)
    target.write_bytes(image.read_bytes() + b"\0" * 4096)
    payload = bytearray(target.read_bytes())
    payload[7] ^= 0xFF
    target.write_bytes(bytes(payload))

    code = af.main(
        [
            "verify",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--verify",
            "full",
            "--no-progress",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_IO
    assert "AIRY-FLASH-FAIL" in out
    assert "偏移 7" in out


def test_cli_missing_digest_blocks_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "AirymaxOS-0.0.3-x86_64.iso"
    image.write_bytes(b"AirymaxOS" * 512)
    target = make_target(tmp_path, 16384)
    before = target.read_bytes()

    code = af.main(
        [
            "write",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--no-progress",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_CHECK
    assert "校验和来源" in out
    assert target.read_bytes() == before


def test_cli_allow_unverified_skips_hash_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "AirymaxOS-0.0.3-x86_64.iso"
    image.write_bytes(b"AirymaxOS" * 512)
    target = make_target(tmp_path, 16384)

    code = af.main(
        [
            "plan",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--allow-unverified",
            "--no-progress",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "[SKIP]" in out


def test_cli_sha256_mismatch_stops_before_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image, _ = make_image(tmp_path)
    target = make_target(tmp_path, 16384)
    before = target.read_bytes()

    code = af.main(
        [
            "write",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--sha256",
            "b" * 64,
            "--no-progress",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_CHECK
    assert "摘要不符" in out
    assert target.read_bytes() == before


def test_cli_json_is_the_only_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image, digest = make_image(tmp_path)
    target = make_target(tmp_path, 16384)

    code = af.main(
        [
            "plan",
            "--image",
            str(image),
            "--device",
            str(target),
            "--allow-file",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["result"] == "pass"
    assert payload["image"]["sha256"] == digest
    assert payload["device"]["kind"] == "file"
    assert [c["name"] for c in payload["checks"]][0] == "目标是块设备"


def test_cli_json_error_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = af.main(["plan", "--image", str(tmp_path / "absent.iso"), "--device", "/dev/null", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_USAGE
    assert payload["result"] == "fail"
    assert "镜像不可读" in payload["error"]


def test_cli_requires_image_and_device() -> None:
    with pytest.raises(SystemExit) as excinfo:
        af.main(["plan"])
    assert excinfo.value.code == EXIT_USAGE


def test_cli_check_human_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert af.main(["check", "--no-progress"]) == EXIT_OK
    assert "AirymaxOS 烧录工具" in capsys.readouterr().out

    assert af.main(["check", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "check"
    assert {c["name"] for c in payload["checks"]} >= {"python", "/proc/mounts"}


def test_write_report_key_value_format(tmp_path: Path) -> None:
    image, digest = make_image(tmp_path)
    report = af.Report(
        command="plan",
        result="pass",
        started="2026-10-25T00:00:00Z",
        finished="2026-10-25T00:00:01Z",
        image=ImageFacts(
            path=image, size=image.stat().st_size, digest=digest, expected=digest
        ),
        device=DeviceFacts(path=Path("/dev/sdb"), kind="block", size=1 << 30),
        checks=[Check("非系统盘", True, "系统盘: sda")],
        stages=[],
        host="b50",
    )
    out = tmp_path / "report.txt"
    af.write_report(report, out)
    text = out.read_text(encoding="utf-8")
    assert "airymax-flash-report v1" in text
    assert "result=pass" in text
    assert f"image.sha256={digest}" in text
    assert "check.非系统盘=pass" in text
