# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""release_manifest 单元测试。

覆盖三块能力：清单 schema 门禁、清单 ↔ 发布流水线双向一致、以及
`--checksums` / `--notes` 两个发布辅助模式；并用合成仓库做端到端验证，
避免任何桩路径。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

import release_manifest as rm

GOOD_MANIFEST = """\
version: "1.0.0"
notes:
  summary:
    - 更新说明示例
  remarks:
    - 其他说明示例
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
        notes = {"summary": [], "remarks": []}
    return {"version": version, "materials": materials, "notes": notes}


def make_repo(path):
    """创建一个仅含目录结构的最小仓库根（供 resolve_paths 校验）。"""
    path.mkdir(parents=True, exist_ok=True)
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
                                 notes={"summary": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes.remarks" in result.details

    def test_notes_rejects_blank_entry(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")],
                                 notes={"summary": [" "], "remarks": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes.summary" in result.details

    def test_notes_rejects_unknown_key(self):
        manifest = manifest_dict([active_material("m", "a/*.rpm")],
                                 notes={"summary": [], "remarks": [],
                                        "bogus": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes 未知字段" in result.details

    def test_notes_rejects_too_many_lines(self):
        manifest = manifest_dict(
            [active_material("m", "a/*.rpm")],
            notes={"summary": ["一", "二", "三", "四"], "remarks": []})
        result = rm.check_schema(manifest)
        assert not result.passed
        assert "notes.summary 至多 3 条" in result.details


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


NOTES_SAMPLE = {"summary": ["甲", "乙"], "remarks": ["丙"]}
NOTES_EMPTY = {"summary": [], "remarks": []}


class TestReleaseNotes:
    def test_sections_in_order(self):
        notes = rm.render_notes("0.0.3", NOTES_SAMPLE)
        assert notes.index("## 概述") < notes.index("## 说明")
        assert notes.index("## 说明") < notes.index("## 其他")

    def test_overview_carries_version_and_date(self):
        notes = rm.render_notes("0.0.3", NOTES_EMPTY)
        assert f"版本号：0.0.3；发布日期：{date.today().isoformat()}" in notes

    def test_disclosures_rendered_as_bullets(self):
        notes = rm.render_notes("0.0.3", NOTES_SAMPLE)
        assert "- 甲" in notes
        assert "- 乙" in notes
        assert "- 丙" in notes

    def test_empty_disclosure_marked(self):
        notes = rm.render_notes("0.0.3", NOTES_EMPTY)
        assert "## 说明\n（无）" in notes
        assert "## 其他\n（无）" in notes

    def test_missing_disclosure_defaults_to_marker(self):
        notes = rm.render_notes("0.0.3")
        assert "## 说明\n（无）" in notes
        assert "## 其他\n（无）" in notes

    def test_total_lines_within_budget(self):
        notes = rm.render_notes("0.0.3", {
            "summary": ["一", "二", "三"],
            "remarks": ["甲", "乙", "丙"],
        })
        assert len([line for line in notes.splitlines() if line.strip()]) <= 12


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
        repo = make_repo(tmp_path / "repo")
        tool = repo / "tools" / "release-engineering"
        tool.mkdir(parents=True, exist_ok=True)
        (tool / "manifest.yaml").write_text(GOOD_MANIFEST, encoding="utf-8")
        out = tmp_path / "notes.md"
        rc = rm.main(["--notes", "--version", "0.0.3",
                      "--repo-root", str(repo), "--output", str(out)])
        assert rc == rm.EXIT_PASS
        text = out.read_text(encoding="utf-8")
        assert "## 概述" in text
        assert "- 更新说明示例" in text
        assert "- 其他说明示例" in text

    def test_notes_requires_ssot_section(self, tmp_path, capsys):
        repo = make_repo(tmp_path / "repo")
        tool = repo / "tools" / "release-engineering"
        tool.mkdir(parents=True, exist_ok=True)
        manifest = manifest_dict([active_material("kernel-rpm", "a/*.rpm")])
        del manifest["notes"]
        (tool / "manifest.yaml").write_text(
            rm.yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
        rc = rm.main(["--notes", "--version", "0.0.3",
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
