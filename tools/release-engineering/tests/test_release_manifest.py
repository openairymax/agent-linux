# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""release_manifest 单元测试。

覆盖三块能力：清单 schema 门禁、清单 ↔ 发布流水线双向一致、以及
`--checksums` / `--notes` 两个发布辅助模式；并用合成仓库与真实 git 历史做
端到端验证，避免任何桩路径。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

import release_manifest as rm

GOOD_MANIFEST = """\
version: "1.0.0"
notes:
  known_issues:
    - 已知边界示例
  upgrade_notes: []
materials:
  - id: kernel-rpm
    name: 内核 RPM
    format: .rpm
    signature: gpg
    authority: docs/x.md §1
    delivery: active
    evidence:
      - dist/rpm/**/*.rpm
  - id: source-tarball
    name: 源码 tarball
    format: .tar.xz
    signature: gpg
    authority: docs/x.md §2
    delivery: deferred
    defer_reason: 待补齐
    defer_target: "0.0.4"
"""

GOOD_RELEASE = """\
name: release
jobs:
  build:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          path: |
            dist/rpm/**/*.rpm
"""

RELEASE_ONLY_OTHER = """\
name: release
jobs:
  build:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          path: |
            other/**/*.rpm
"""

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Alice",
    "GIT_AUTHOR_EMAIL": "alice@example.com",
    "GIT_COMMITTER_NAME": "Alice",
    "GIT_COMMITTER_EMAIL": "alice@example.com",
}


# ─── Fixtures / helpers ────────────────────────────────────────────────────


def load_manifest_text(text):
    """把清单文本解析为 dict（绕过文件系统）。"""
    return rm.yaml.safe_load(text)


def write_repo(tmp_path, manifest_text=GOOD_MANIFEST,
               release_text=GOOD_RELEASE):
    """在合成仓库写入清单与发布流水线，返回清单路径。"""
    tool = tmp_path / "tools" / "release-engineering"
    tool.mkdir(parents=True, exist_ok=True)
    manifest = tool / "manifest.yaml"
    manifest.write_text(manifest_text, encoding="utf-8")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "release.yml").write_text(release_text, encoding="utf-8")
    return manifest


def write_release(tmp_path, text):
    """只写发布流水线（用于一致性反向用例）。"""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / "release.yml"
    path.write_text(text, encoding="utf-8")
    return path


def active_material(mid, *globs):
    """构造一条 active 物料记录。"""
    return {"id": mid, "name": mid, "format": ".bin", "signature": "gpg",
            "authority": "docs/x.md §1", "delivery": "active",
            "evidence": list(globs)}


def manifest_dict(materials, version="1.0.0", notes=None):
    """构造顶层清单结构（notes 缺省为合法披露段）。"""
    if notes is None:
        notes = {"known_issues": [], "upgrade_notes": []}
    return {"version": version, "materials": materials, "notes": notes}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True,
                          env={**os.environ, **_GIT_ENV})


def _commit(repo, name, content, subject):
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", subject)


def make_git_repo(path):
    """初始化一个带若干提交的最小 git 仓库。"""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _commit(path, "a.txt", "1", "mm: fix high-order allocation stall")
    _commit(path, "b.txt", "2", "net: add io_uring support")
    _commit(path, "c.txt", "3", "security: fix CVE-2026-0001 in airy lsm")
    return path


# ─── Schema ────────────────────────────────────────────────────────────────


class TestManifestSchema:
    def test_good_manifest_passes(self):
        assert rm.check_schema(load_manifest_text(GOOD_MANIFEST)).passed

    def test_unknown_top_key(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")], version="")
        manifest["extra"] = 1
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "顶层未知字段" in result.details

    def test_missing_version(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")], version="")
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "version" in result.details

    def test_materials_must_be_non_empty_list(self):
        result = rm.check_schema(manifest_dict([]))
        assert not result.passed
        assert "materials 必须是非空列表" in result.details

    def test_duplicate_id(self):
        materials = [active_material("dup", "a/*.rpm"),
                     active_material("dup", "b/*.rpm")]
        result = rm.check_schema(manifest_dict(materials))
        assert not result.passed
        assert "重复" in result.details

    def test_active_requires_evidence(self):
        item = active_material("m")
        item["evidence"] = []
        result = rm.check_schema(manifest_dict([item]))
        assert not result.passed
        assert "非空 evidence" in result.details

    def test_active_rejects_defer_fields(self):
        item = active_material("m", "a/*.rpm")
        item["defer_reason"] = "x"
        item["defer_target"] = "0.0.4"
        result = rm.check_schema(manifest_dict([item]))
        assert not result.passed
        assert "不得登记 defer_reason" in result.details

    def test_deferred_requires_reason_and_target(self):
        item = {"id": "m", "name": "m", "format": ".rpm", "signature": "gpg",
                "authority": "docs/x.md §1", "delivery": "deferred"}
        result = rm.check_schema(manifest_dict([item]))
        assert not result.passed
        assert "defer_reason" in result.details
        assert "defer_target" in result.details

    def test_deferred_rejects_evidence(self):
        item = {"id": "m", "name": "m", "format": ".rpm", "signature": "gpg",
                "authority": "docs/x.md §1", "delivery": "deferred",
                "defer_reason": "x", "defer_target": "0.0.4",
                "evidence": ["a/*.rpm"]}
        result = rm.check_schema(manifest_dict([item]))
        assert not result.passed
        assert "不得登记 evidence" in result.details

    def test_bad_delivery_value(self):
        item = active_material("m", "a/*.rpm")
        item["delivery"] = "pending"
        result = rm.check_schema(manifest_dict([item]))
        assert not result.passed
        assert "delivery" in result.details

    def test_unknown_material_field(self):
        item = active_material("m", "a/*.rpm")
        item["bogus"] = "x"
        result = rm.check_schema(manifest_dict([item]))
        assert not result.passed
        assert "未知字段" in result.details

    def test_missing_notes_section(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")])
        del manifest["notes"]
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes" in result.details

    def test_notes_requires_both_keys(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")],
                                 notes={"known_issues": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes.upgrade_notes" in result.details

    def test_notes_rejects_blank_entry(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")],
                                 notes={"known_issues": [" "],
                                        "upgrade_notes": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes.known_issues" in result.details

    def test_notes_rejects_unknown_key(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")],
                                 notes={"known_issues": [], "upgrade_notes": [],
                                        "bogus": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes 未知字段" in result.details


# ─── Pipeline extraction ───────────────────────────────────────────────────


class TestPipelineExtraction:
    def test_upload_and_release_globs_normalized(self, tmp_path):
        wf = write_release(tmp_path, """\
jobs:
  build:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          path: |
            dist/rpm/**/*.rpm
            sbom-agent-linux-${{ steps.ver.outputs.ver }}.spdx.json
  publish:
    steps:
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/rpm/**/*.rpm
            agent-linux-sdk-*.tar.gz
""")
        assert rm.pipeline_globs(wf) == {
            "dist/rpm/**/*.rpm",
            "sbom-agent-linux-*.spdx.json",
            "agent-linux-sdk-*.tar.gz",
        }

    def test_missing_jobs_raises(self, tmp_path):
        wf = tmp_path / "release.yml"
        wf.write_text("name: x\n", encoding="utf-8")
        with pytest.raises(rm.ToolError):
            rm.pipeline_globs(wf)

    def test_no_declared_output_raises(self, tmp_path):
        wf = write_release(tmp_path, "jobs:\n  build:\n    steps: []\n")
        with pytest.raises(rm.ToolError):
            rm.pipeline_globs(wf)


# ─── Pipeline consistency ──────────────────────────────────────────────────


class TestPipelineConsistency:
    def test_consistent_passes(self, tmp_path):
        write_repo(tmp_path)
        manifest = load_manifest_text(GOOD_MANIFEST)
        assert rm.check_pipeline(manifest, tmp_path).passed

    def test_active_evidence_missing_from_pipeline(self, tmp_path):
        write_repo(tmp_path)
        manifest = manifest_dict([active_material(
            "kernel-rpm", "dist/rpm/**/*.rpm", "extra/**/*.zip")])
        result = rm.check_pipeline(manifest, tmp_path)
        assert not result.passed
        assert "extra/**/*.zip 未在 release.yml 声明" in result.details

    def test_unregistered_pipeline_output(self, tmp_path):
        write_repo(tmp_path, release_text=GOOD_RELEASE.replace(
            "dist/rpm/**/*.rpm", "rogue/**/*.pkg"))
        manifest = manifest_dict([active_material("kernel-rpm",
                                                  "dist/rpm/**/*.rpm")])
        result = rm.check_pipeline(manifest, tmp_path)
        assert not result.passed
        assert "rogue/**/*.pkg 未在清单登记" in result.details

    def test_missing_pipeline_file(self, tmp_path):
        manifest = manifest_dict([active_material("m", "a/*.rpm")])
        result = rm.check_pipeline(manifest, tmp_path)
        assert not result.passed
        assert "不存在" in result.details


# ─── Checksums ─────────────────────────────────────────────────────────────


class TestChecksums:
    def test_deterministic_sorted_output(self, tmp_path):
        (tmp_path / "b.bin").write_bytes(b"world")
        (tmp_path / "a.bin").write_bytes(b"hello")
        text = rm.build_sums(
            [tmp_path / "b.bin", tmp_path / "a.bin"])
        lines = text.splitlines()
        assert lines[0] == f"{hashlib.sha256(b'hello').hexdigest()}  a.bin"
        assert lines[1] == f"{hashlib.sha256(b'world').hexdigest()}  b.bin"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(rm.ToolError):
            rm.build_sums([tmp_path / "nope.bin"])

    def test_empty_input_raises(self):
        with pytest.raises(rm.ToolError):
            rm.build_sums([])


# ─── Release notes ─────────────────────────────────────────────────────────


class TestReleaseNotes:
    @pytest.mark.parametrize("subject,expected", [
        ("security: fix CVE-2026-0001 in airy lsm", "security"),
        ("fix: null deref in logger", "fix"),
        ("mm: fix page leak", "fix"),
        ("net: add gateway support", "feature"),
        ("docs: update readme", "feature"),
    ])
    def test_classify_commit(self, subject, expected):
        assert rm.classify_commit(subject) == expected

    def test_collect_git_log(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        commits = rm.collect_git_log(repo, "HEAD")
        assert len(commits) == 3
        assert all(len(item) == 3 for item in commits)
        assert {item[2] for item in commits} == {"Alice"}

    def test_render_notes_sections(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        commits = rm.collect_git_log(repo, "HEAD")
        notes = rm.render_notes("v0.0.3", "HEAD", commits, "abc123  x.rpm\n")
        assert notes.startswith("# v0.0.3 Release Notes")
        assert "## 修复的缺陷" in notes
        assert "## 安全修复" in notes
        assert "CVE-2026-0001" in notes
        assert "abc123  x.rpm" in notes
        assert "- Alice (3)" in notes

    def test_render_notes_without_checksums(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        commits = rm.collect_git_log(repo, "HEAD")
        notes = rm.render_notes("v0.0.3", "HEAD", commits, "")
        assert rm.NOTES_NOT_PROVIDED in notes

    def test_render_notes_discloses_ssot(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        commits = rm.collect_git_log(repo, "HEAD")
        notes = rm.render_notes("v0.0.3", "HEAD", commits, "",
                                {"known_issues": ["边界 A"],
                                 "upgrade_notes": ["迁移 B"]})
        assert "## 已知问题" in notes
        assert "- 边界 A" in notes
        assert "## 升级说明" in notes
        assert "- 迁移 B" in notes

    def test_render_notes_empty_disclosure_is_none(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        commits = rm.collect_git_log(repo, "HEAD")
        notes = rm.render_notes("v0.0.3", "HEAD", commits, "",
                                {"known_issues": [], "upgrade_notes": []})
        assert "## 已知问题\n\n（无）" in notes
        assert "## 升级说明\n\n（无）" in notes

    def test_render_notes_missing_disclosure_marked(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        commits = rm.collect_git_log(repo, "HEAD")
        notes = rm.render_notes("v0.0.3", "HEAD", commits, "",
                                {"known_issues": []})
        known = notes.split("## 已知问题", 1)[1].split("##", 1)[0]
        upgrade = notes.split("## 升级说明", 1)[1].split("##", 1)[0]
        assert rm.NOTES_NOT_PROVIDED not in known
        assert rm.NOTES_NOT_PROVIDED in upgrade

    def test_collect_git_log_bad_range(self, tmp_path):
        repo = make_git_repo(tmp_path / "gitrepo")
        with pytest.raises(rm.ToolError):
            rm.collect_git_log(repo, "no-such-ref..HEAD")


# ─── CLI ───────────────────────────────────────────────────────────────────


class TestCli:
    def test_check_pass(self, tmp_path, capsys):
        manifest = write_repo(tmp_path)
        rc = rm.main(["--check", "--repo-root", str(tmp_path),
                      "--manifest", str(manifest)])
        assert rc == rm.EXIT_PASS
        assert "RELEASE-MANIFEST: PASS" in capsys.readouterr().out

    def test_check_fail_on_pipeline_mismatch(self, tmp_path, capsys):
        manifest = write_repo(tmp_path, release_text=RELEASE_ONLY_OTHER)
        rc = rm.main(["--check", "--repo-root", str(tmp_path),
                      "--manifest", str(manifest)])
        assert rc == rm.EXIT_FAIL
        assert "RELEASE-MANIFEST: FAIL" in capsys.readouterr().out

    def test_verbose_report_is_default(self, tmp_path, capsys):
        manifest = write_repo(tmp_path)
        rc = rm.main(["--repo-root", str(tmp_path),
                      "--manifest", str(manifest)])
        assert rc == rm.EXIT_PASS
        assert "RESULT: PASS" in capsys.readouterr().out

    def test_checksums_mode(self, tmp_path, capsys):
        target = tmp_path / "x.bin"
        target.write_bytes(b"data")
        rc = rm.main(["--checksums", str(target)])
        assert rc == rm.EXIT_PASS
        assert "x.bin" in capsys.readouterr().out

    def test_checksums_missing_file_usage_error(self, tmp_path, capsys):
        rc = rm.main(["--checksums", str(tmp_path / "nope")])
        assert rc == rm.EXIT_USAGE
        assert "不存在" in capsys.readouterr().err

    def test_checksums_writes_output_file(self, tmp_path, capsys):
        target = tmp_path / "x.bin"
        target.write_bytes(b"data")
        out = tmp_path / "sums.txt"
        rc = rm.main(["--checksums", "--output", str(out), str(target)])
        assert rc == rm.EXIT_PASS
        assert "x.bin" in out.read_text(encoding="utf-8")

    def test_output_dir_usage_error(self, tmp_path, capsys):
        target = tmp_path / "x.bin"
        target.write_bytes(b"data")
        rc = rm.main(["--checksums", "--output", str(tmp_path), str(target)])
        assert rc == rm.EXIT_USAGE
        assert "无法写入" in capsys.readouterr().err

    def test_notes_requires_version(self, capsys):
        assert rm.main(["--notes"]) == rm.EXIT_USAGE
        assert "--version" in capsys.readouterr().err

    def test_notes_generates_file(self, tmp_path, capsys):
        repo = make_git_repo(tmp_path / "gitrepo")
        tool = repo / "tools" / "release-engineering"
        tool.mkdir(parents=True, exist_ok=True)
        (tool / "manifest.yaml").write_text(GOOD_MANIFEST, encoding="utf-8")
        out = tmp_path / "notes.md"
        rc = rm.main(["--notes", "--version", "v0.0.3",
                      "--repo-root", str(repo), "--output", str(out)])
        assert rc == rm.EXIT_PASS
        text = out.read_text(encoding="utf-8")
        assert "Release Notes" in text
        assert "- 已知边界示例" in text

    def test_notes_requires_ssot_section(self, tmp_path, capsys):
        repo = make_git_repo(tmp_path / "gitrepo")
        tool = repo / "tools" / "release-engineering"
        tool.mkdir(parents=True, exist_ok=True)
        manifest = manifest_dict([active_material("kernel-rpm", "a/*.rpm")])
        del manifest["notes"]
        (tool / "manifest.yaml").write_text(
            rm.yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
        rc = rm.main(["--notes", "--version", "v0.0.3",
                      "--repo-root", str(repo)])
        assert rc == rm.EXIT_USAGE
        assert "notes" in capsys.readouterr().err

    def test_missing_manifest_usage_error(self, tmp_path, capsys):
        rc = rm.main(["--check", "--repo-root", str(tmp_path),
                      "--manifest", str(tmp_path / "no.yaml")])
        assert rc == rm.EXIT_USAGE
        assert "不存在" in capsys.readouterr().err

    def test_repo_root_missing_usage_error(self, tmp_path, capsys):
        rc = rm.main(["--check", "--repo-root", str(tmp_path / "ghost")])
        assert rc == rm.EXIT_USAGE
        assert "仓库根不存在" in capsys.readouterr().err


# ─── Real repository ───────────────────────────────────────────────────────


class TestRealRepo:
    def test_real_repo_passes(self, repo_root, capsys):
        rc = rm.main(["--check", "--repo-root", str(repo_root)])
        assert rc == rm.EXIT_PASS
        assert "RELEASE-MANIFEST: PASS" in capsys.readouterr().out
