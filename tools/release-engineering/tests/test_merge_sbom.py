# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""merge_sbom.py 单元测试。

覆盖：分片合并（包/文件/关系/外部引用/许可信息并集）、去重与冲突判定、
文档级固定字段校验、SOURCE_DATE_EPOCH 派生时间戳、输出的确定性与排序、
CLI 退出码（0/1/2）与摘要输出。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import merge_sbom


def _pkg(spdx_id, name=None):
    """构造一个最小 SPDX 包对象。"""
    return {"SPDXID": spdx_id, "name": name or spdx_id, "versionInfo": "1.0"}


def _rel(src, rtype, dst):
    """构造一个最小 SPDX 关系对象。"""
    return {"spdxElementId": src, "relationshipType": rtype,
            "relatedSpdxElement": dst}


def _doc(packages=(), relationships=(), creators=("Tool: syft-1.0",),
         llv=None, version="SPDX-2.3", data_license="CC0-1.0"):
    """构造一个 SPDX 2.3 分片文档。"""
    creation = {"creators": list(creators), "created": "2020-01-01T00:00:00Z"}
    if llv:
        creation["licenseListVersion"] = llv
    return {
        "spdxVersion": version,
        "dataLicense": data_license,
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "fragment",
        "documentNamespace": "https://example.com/spdx/fragment",
        "creationInfo": creation,
        "packages": list(packages),
        "relationships": list(relationships),
    }


def _write(tmp_path, name, doc):
    """落盘一个分片并返回其路径字符串。"""
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def _run(tmp_path, inputs, out_name="merged.spdx.json", extra=()):
    """以给定分片调用 CLI，返回退出码与输出路径。"""
    out = tmp_path / out_name
    argv = ["--output", str(out), *extra, *inputs]
    return merge_sbom.main(argv), out


# ─── 合并语义 ──────────────────────────────────────────────────────────────


def test_merges_packages_and_relationships(tmp_path):
    a = _write(tmp_path, "a.json", _doc(
        packages=[_pkg("SPDXRef-Package-kernel")],
        relationships=[_rel("SPDXRef-Package-agent-linux", "CONTAINS",
                            "SPDXRef-Package-kernel")]))
    b = _write(tmp_path, "b.json", _doc(
        packages=[_pkg("SPDXRef-Package-memory")],
        relationships=[_rel("SPDXRef-Package-kernel", "DEPENDS_ON",
                            "SPDXRef-Package-memory")]))

    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    ids = [pkg["SPDXID"] for pkg in merged["packages"]]
    assert ids == ["SPDXRef-Package-kernel", "SPDXRef-Package-memory"]
    assert len(merged["relationships"]) == 2


def test_document_level_fields_are_fixed(tmp_path):
    a = _write(tmp_path, "a.json", _doc(packages=[_pkg("SPDXRef-Package-x")]))
    code, out = _run(tmp_path, [a], extra=["--name", "agent-linux-v0.3.0"])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["spdxVersion"] == "SPDX-2.3"
    assert merged["dataLicense"] == "CC0-1.0"
    assert merged["SPDXID"] == "SPDXRef-DOCUMENT"
    assert merged["name"] == "agent-linux-v0.3.0"
    assert merged["documentNamespace"] == \
        "https://airymaxos.com/spdx/agent-linux-v0.3.0"


def test_explicit_namespace_overrides_default(tmp_path):
    a = _write(tmp_path, "a.json", _doc(packages=[_pkg("SPDXRef-Package-x")]))
    code, out = _run(tmp_path, [a], extra=[
        "--namespace", "https://example.com/custom-ns"])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["documentNamespace"] == "https://example.com/custom-ns"


def test_identical_package_is_deduplicated(tmp_path):
    shared = _pkg("SPDXRef-Package-kernel")
    a = _write(tmp_path, "a.json", _doc(packages=[shared]))
    b = _write(tmp_path, "b.json", _doc(packages=[dict(shared)]))
    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert len(merged["packages"]) == 1


def test_conflicting_package_fails(tmp_path, capsys):
    a = _write(tmp_path, "a.json", _doc(
        packages=[_pkg("SPDXRef-Package-x", name="one")]))
    b = _write(tmp_path, "b.json", _doc(
        packages=[_pkg("SPDXRef-Package-x", name="two")]))
    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_FAIL
    assert "内容冲突" in capsys.readouterr().err
    assert not out.exists()


def test_relationship_triples_are_deduplicated(tmp_path):
    rel = _rel("SPDXRef-Package-a", "DEPENDS_ON", "SPDXRef-Package-b")
    a = _write(tmp_path, "a.json", _doc(relationships=[rel]))
    b = _write(tmp_path, "b.json", _doc(relationships=[dict(rel)]))
    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["relationships"] == [rel]


def test_files_and_licensing_and_external_refs_union(tmp_path):
    a = _write(tmp_path, "a.json", {
        **_doc(),
        "files": [{"SPDXID": "SPDXRef-File-syscall-table.c",
                   "fileName": "./syscall-table.c"}],
        "externalDocumentRefs": [{"externalDocumentId": "DocumentRef-rustc",
                                  "spdxDocument": "https://example.com/x"}],
        "hasExtractedLicensingInfos": [{"licenseId": "LicenseRef-custom"}],
    })
    b = _write(tmp_path, "b.json", {
        **_doc(),
        "files": [{"SPDXID": "SPDXRef-File-ipc-gen.c",
                   "fileName": "./ipc-gen.c"}],
    })
    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert [f["SPDXID"] for f in merged["files"]] == \
        ["SPDXRef-File-ipc-gen.c", "SPDXRef-File-syscall-table.c"]
    assert merged["externalDocumentRefs"][0]["externalDocumentId"] == \
        "DocumentRef-rustc"
    assert merged["hasExtractedLicensingInfos"][0]["licenseId"] == \
        "LicenseRef-custom"


def test_creators_union_includes_tool(tmp_path):
    a = _write(tmp_path, "a.json", _doc(
        creators=("Organization: SPHARX Ltd.", "Tool: syft-1.0")))
    b = _write(tmp_path, "b.json", _doc(creators=("Tool: syft-1.1",)))
    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_PASS
    creators = json.loads(out.read_text(encoding="utf-8"))["creationInfo"]["creators"]
    assert creators == sorted([
        "Organization: SPHARX Ltd.", "Tool: syft-1.0", "Tool: syft-1.1",
        merge_sbom.TOOL_CREATOR])


def test_license_list_version_takes_max(tmp_path):
    a = _write(tmp_path, "a.json", _doc(llv="3.9"))
    b = _write(tmp_path, "b.json", _doc(llv="3.23"))
    code, out = _run(tmp_path, [a, b])

    assert code == merge_sbom.EXIT_PASS
    info = json.loads(out.read_text(encoding="utf-8"))["creationInfo"]
    assert info["licenseListVersion"] == "3.23"


# ─── 固定字段 / 语义校验 ───────────────────────────────────────────────────


def test_version_mismatch_fails(tmp_path, capsys):
    a = _write(tmp_path, "a.json", _doc(version="SPDX-2.2"))
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_FAIL
    assert "spdxVersion" in capsys.readouterr().err


def test_datalicense_mismatch_fails(tmp_path, capsys):
    a = _write(tmp_path, "a.json", _doc(data_license="MIT"))
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_FAIL
    assert "dataLicense" in capsys.readouterr().err


def test_missing_spdx_version_fails(tmp_path):
    doc = _doc(packages=[_pkg("SPDXRef-Package-x")])
    del doc["spdxVersion"]
    a = _write(tmp_path, "a.json", doc)
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_FAIL


def test_package_without_spdxid_fails(tmp_path, capsys):
    doc = _doc(packages=[{"name": "no-id"}])
    a = _write(tmp_path, "a.json", doc)
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_FAIL
    assert "SPDXID" in capsys.readouterr().err


def test_relationship_missing_field_fails(tmp_path):
    doc = _doc(relationships=[{"spdxElementId": "SPDXRef-A"}])
    a = _write(tmp_path, "a.json", doc)
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_FAIL


# ─── 用法 / 解析错误 ───────────────────────────────────────────────────────


def test_no_inputs_returns_usage(tmp_path, capsys):
    code = merge_sbom.main(["--output", str(tmp_path / "o.json")])

    assert code == merge_sbom.EXIT_USAGE
    assert "至少需要一个输入分片" in capsys.readouterr().err


def test_missing_file_returns_usage(tmp_path, capsys):
    code, _ = _run(tmp_path, [str(tmp_path / "absent.json")])

    assert code == merge_sbom.EXIT_USAGE
    assert "无法读取" in capsys.readouterr().err


def test_invalid_json_returns_usage(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _ = _run(tmp_path, [str(bad)])

    assert code == merge_sbom.EXIT_USAGE
    assert "JSON" in capsys.readouterr().err


def test_non_object_top_level_returns_usage(tmp_path):
    bad = tmp_path / "list.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    code, _ = _run(tmp_path, [str(bad)])

    assert code == merge_sbom.EXIT_USAGE


def test_invalid_source_date_epoch_returns_usage(tmp_path, monkeypatch):
    a = _write(tmp_path, "a.json", _doc())
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-a-number")
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_USAGE


# ─── 可重复构建 ────────────────────────────────────────────────────────────


def test_source_date_epoch_derives_created(tmp_path, monkeypatch):
    a = _write(tmp_path, "a.json", _doc())
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    code, out = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_PASS
    created = json.loads(out.read_text(encoding="utf-8"))["creationInfo"]["created"]
    expected = datetime.fromtimestamp(1700000000, tz=timezone.utc)
    assert created == expected.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_output_is_byte_deterministic(tmp_path, monkeypatch):
    a = _write(tmp_path, "a.json", _doc(
        packages=[_pkg("SPDXRef-Package-memory"), _pkg("SPDXRef-Package-kernel")],
        relationships=[_rel("SPDXRef-Package-kernel", "DEPENDS_ON",
                            "SPDXRef-Package-memory")]))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")

    code1, out1 = _run(tmp_path, [a], out_name="one.json")
    code2, out2 = _run(tmp_path, [a], out_name="two.json")

    assert code1 == code2 == merge_sbom.EXIT_PASS
    assert out1.read_bytes() == out2.read_bytes()


def test_packages_sorted_by_spdxid(tmp_path):
    a = _write(tmp_path, "a.json", _doc(packages=[
        _pkg("SPDXRef-Package-system"),
        _pkg("SPDXRef-Package-kernel"),
        _pkg("SPDXRef-Package-memory"),
    ]))
    code, out = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_PASS
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert [pkg["SPDXID"] for pkg in merged["packages"]] == [
        "SPDXRef-Package-kernel", "SPDXRef-Package-memory",
        "SPDXRef-Package-system"]


# ─── 摘要输出 / CLI ────────────────────────────────────────────────────────


def test_summary_is_printed(tmp_path, capsys):
    a = _write(tmp_path, "a.json", _doc(packages=[_pkg("SPDXRef-Package-x")]))
    code, _ = _run(tmp_path, [a])

    assert code == merge_sbom.EXIT_PASS
    assert "MERGE-SBOM: PASS" in capsys.readouterr().out


def test_quiet_suppresses_summary(tmp_path, capsys):
    a = _write(tmp_path, "a.json", _doc(packages=[_pkg("SPDXRef-Package-x")]))
    code, _ = _run(tmp_path, [a], extra=["--quiet"])

    assert code == merge_sbom.EXIT_PASS
    assert capsys.readouterr().out == ""


def test_reference_release_workflow_uses_this_tool(repo_root):
    """release.yml 必须以新增 step 直接调用本工具，且不存在 SKIP 桩路径。"""
    workflow = (repo_root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8")
    assert "tools/release-engineering/merge_sbom.py" in workflow
    assert "SKIP" not in workflow
