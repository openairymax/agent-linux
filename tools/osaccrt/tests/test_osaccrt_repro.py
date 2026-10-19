# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_repro 单元测试：脚本内容、离线校验与命令重放。"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import osaccrt_evidence as oe
import osaccrt_model as om
import osaccrt_repro as orep
import osaccrt_store as os_

DATE = "2026-01-19"
IMAGE = "b" * 64
HOST = "qemu-x86_64"
PREP = "step-01-prepare.json"
RUN = "step-02-run.log"
CAP = "step-03-artifacts"
VERDICT = "step-04-verdict.json"
EXP = "exp-001"
SH = shutil.which("sh")


def make_th(key: str = "boot.time") -> om.Threshold:
    """构造引自 OS-ACC 原文的合法阈值。"""
    return om.Threshold(key, "OS-ACC-BOOT-010", "60", "s")


def make_man(verdict: om.Verdict = om.Verdict.PASS,
             **fields: object) -> oe.Manifest:
    """构造不入库的清单，用于渲染分支覆盖。"""
    result = om.ExpResult(EXP, (make_th(),), verdict=verdict, **fields)
    return oe.Manifest(DATE, HOST, IMAGE, result)


def make_pack(
    tmp_path: Path, verdict: om.Verdict = om.Verdict.PASS
) -> tuple[Path, oe.Manifest]:
    """在临时目录内生成真实封口包，返回包路径与清单。"""
    store = os_.Store(tmp_path / "evidence")
    store.create(DATE, EXP)
    result = om.ExpResult(EXP, (make_th(),), verdict=verdict)
    files = (
        (PREP, om.Step.PREPARE, b"{}\n"),
        (RUN, om.Step.RUN, b"boot ok\n"),
        (f"{CAP}/boot.time", om.Step.CAPTURE, b"12.5\n"),
        (VERDICT, om.Step.VERDICT, b'{"verdict": "PASS"}\n'),
    )
    for rel, step, data in files:
        digest = store.write(DATE, EXP, rel, data)
        result.evidences.append(om.Evidence(rel, digest, step))
    store.seal(oe.Manifest(DATE, HOST, IMAGE, result))
    return store.pack(DATE, EXP), store.load(DATE, EXP)


def write_script(tmp_path: Path, man: oe.Manifest,
                 command: str = "true") -> Path:
    """把复现脚本写到临时目录，默认证据根为 evidence。"""
    return orep.emit(man, command, tmp_path / "replay.sh")


def run_sh(script: Path, *args: str,
           cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """执行复现脚本并收集输出；缺少 sh 时跳过用例。"""
    if SH is None:
        pytest.skip("sh is not available")
    return subprocess.run(
        [SH, str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_carries_metadata(tmp_path: Path) -> None:
    """脚本头部登记实验编号、宿主、镜像、判定、链头与阈值。"""
    _pack, man = make_pack(tmp_path)
    text = orep.render(man, "true")
    assert "# exp-id: exp-001" in text
    assert f"# date: {DATE}" in text
    assert f"# host: {HOST}" in text
    assert f"# image-sha256: {IMAGE}" in text
    assert "# verdict: PASS" in text
    assert f"# chain-head: {man.chain_head}" in text
    assert "# threshold: boot.time=60 s (OS-ACC-BOOT-010)" in text
    assert f"# pack-dir defaults to evidence/{DATE}/{EXP}" in text


def test_render_lists_every_digest(tmp_path: Path) -> None:
    """摘要清单覆盖清单文件本身与全部证据。"""
    _pack, man = make_pack(tmp_path)
    text = orep.render(man, "true")
    assert "  manifest.json" in text
    for item in man.result.evidences:
        assert f"{item.sha256}  {item.path}" in text


def test_script_is_posix_valid(tmp_path: Path) -> None:
    """生成的脚本必须是语法合法的 POSIX sh。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    if SH is None:
        pytest.skip("sh is not available")
    result = subprocess.run(
        [SH, "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_script_verifies_real_pack(tmp_path: Path) -> None:
    """真实封口包在默认路径下通过校验并退出 0。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    result = run_sh(script, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"evidence pack verified: {EXP}" in result.stdout


def test_script_rejects_tampered_evidence(tmp_path: Path) -> None:
    """证据内容被改写后摘要校验失败，脚本拒绝复现。"""
    pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    target = pack / RUN
    os.chmod(pack, 0o755)
    os.chmod(target, 0o644)
    target.write_text("boot failed\n", encoding="utf-8")
    result = run_sh(script, cwd=tmp_path)
    assert result.returncode != 0
    assert "verified" not in result.stdout


def test_script_rejects_chain_mismatch(tmp_path: Path) -> None:
    """链头被替换时校验失败，即使证据摘要未变。"""
    pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    chain = pack / "chain.sha256"
    os.chmod(pack, 0o755)
    os.chmod(chain, 0o644)
    chain.write_text("0" * 64 + "\n", encoding="ascii")
    result = run_sh(script, cwd=tmp_path)
    assert result.returncode != 0
    assert "chain head mismatch" in result.stderr


def test_script_replays_command(tmp_path: Path) -> None:
    """传入 --run 时在临时目录执行原始命令。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man, "printf replayed")
    result = run_sh(script, "--run", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "replaying in" in result.stdout
    assert result.stdout.rstrip().endswith("replayed")


def test_script_without_run_stops(tmp_path: Path) -> None:
    """默认只校验不重放，并提示开启方式。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man, "printf replayed")
    result = run_sh(script, cwd=tmp_path)
    assert result.returncode == 0
    assert "pass --run" in result.stdout
    assert "replayed" not in result.stdout


def test_script_rejects_unknown_option(tmp_path: Path) -> None:
    """未知选项以用法错误退出，不触碰证据。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    result = run_sh(script, "--bogus", cwd=tmp_path)
    assert result.returncode == 2
    assert "unknown option" in result.stderr


def test_script_rejects_missing_pack(tmp_path: Path) -> None:
    """证据包目录不存在时以非零退出。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    result = run_sh(script, str(tmp_path / "absent"), cwd=tmp_path)
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_render_rejects_bad_command(tmp_path: Path) -> None:
    """命令必须是非空单行文本，禁止折行注入。"""
    _pack, man = make_pack(tmp_path)
    for command in ("", " true", "true\ntrue", "true\r"):
        with pytest.raises(ValueError, match="command"):
            orep.render(man, command)


def test_render_rejects_undecided() -> None:
    """未判定的证据包不能被复现。"""
    man = oe.Manifest(
        DATE, HOST, IMAGE, om.ExpResult(EXP, (make_th(),))
    )
    with pytest.raises(ValueError, match="undecided"):
        orep.render(man, "true")


def test_render_rejects_non_manifest() -> None:
    """拒绝鸭子类型输入。"""
    with pytest.raises(TypeError, match="Manifest"):
        orep.render(object(), "true")  # type: ignore[arg-type]


def test_render_rejects_bad_root(tmp_path: Path) -> None:
    """证据根必须是路径安全的相对目录。"""
    _pack, man = make_pack(tmp_path)
    for root in ("/evidence", "evi dence", "../evidence", ""):
        with pytest.raises(ValueError, match="pack_root"):
            orep.render(man, "true", root)


def test_render_notes_waiver_and_blocked() -> None:
    """降级与阻塞状态在头部留下可追溯记录。"""
    waived = make_man(
        om.Verdict.WAIVED,
        waiver_sig=("lead@example.com", "review@example.com"),
    )
    blocked = make_man(
        om.Verdict.BLOCKED, blocked_reason="no sw_64 hardware"
    )
    assert "# waiver-sig: lead@example.com, review@example.com" in (
        orep.render(waived, "true")
    )
    assert "# blocked-reason: no sw_64 hardware" in (
        orep.render(blocked, "true")
    )


def test_render_custom_pack_root(tmp_path: Path) -> None:
    """自定义证据根同步反映在默认路径与用法注释中。"""
    _pack, man = make_pack(tmp_path)
    text = orep.render(man, "true", "artifacts/runs")
    assert f"PACK='artifacts/runs/{DATE}/{EXP}'" in text
    assert f"# pack-dir defaults to artifacts/runs/{DATE}/{EXP}" in text


def test_emit_is_executable(tmp_path: Path) -> None:
    """写出文件带可执行位且内容与渲染一致。"""
    _pack, man = make_pack(tmp_path)
    script = write_script(tmp_path, man)
    mode = stat.S_IMODE(script.stat().st_mode)
    assert mode & 0o111
    assert script.read_text(encoding="utf-8") == orep.render(man, "true")
