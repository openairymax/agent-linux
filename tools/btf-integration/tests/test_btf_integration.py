# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/btf-integration 单元测试：清单 schema / 受检片段存在性 / eBPF 基线 /
BTF 基线 / tracing 链路 / CI 供给 / build_checks 编排 / CLI 与真实仓库端到端。

测试以 tmp_path 构造受控合成仓库，逐项断言各检查器的通过/拒绝语义；另有一例
在真实仓库上运行，作为端到端回归。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

from pathlib import Path

import pytest

import btf_integration as bi

GOOD_ARCHES: list[dict[str, str]] = [
    {"arch": "x86_64", "configs": "arch/x86/configs/airy_defconfig"},
    {"arch": "arm64", "configs": "arch/arm64/configs/airy_defconfig"},
]

BIO_MANIFEST = """\
version: "1.0.0"

defconfig_matrix_manifest: tools/defconfig-matrix/manifest.yaml

bpf_configs:
  - CONFIG_BPF=y
  - CONFIG_BPF_SYSCALL=y

btf_configs:
  - CONFIG_DEBUG_INFO_BTF=y

btf_mutex_unset:
  - CONFIG_DEBUG_INFO_REDUCED
  - CONFIG_DEBUG_INFO_SPLIT

debug_info_choice:
  - CONFIG_DEBUG_INFO_DWARF4=y

tracing_chain:
  - CONFIG_FTRACE=y
  - CONFIG_KPROBES=y

pahole_package: dwarves

btf_artifacts:
  - .BTF

ci_workflows:
  - .github/workflows/ci-kernel.yml
"""

GOOD_FRAGMENT = (
    "# SPDX-License-Identifier: GPL-2.0-only\n"
    "CONFIG_BPF=y\n"
    "CONFIG_BPF_SYSCALL=y\n"
    "CONFIG_DEBUG_INFO_BTF=y\n"
    "# CONFIG_DEBUG_INFO_REDUCED is not set\n"
    "# CONFIG_DEBUG_INFO_SPLIT is not set\n"
    "CONFIG_DEBUG_INFO_DWARF4=y\n"
    "CONFIG_FTRACE=y\n"
    "CONFIG_KPROBES=y\n"
)

GOOD_CI = (
    "jobs:\n"
    "  kernel-verify:\n"
    "    steps:\n"
    "      - name: install\n"
    "        run: sudo apt-get install -y dwarves\n"
    "      - name: assert btf\n"
    "        run: readelf -S vmlinux | grep -q '.BTF'\n"
)


def _matrix_text(arches: list[dict[str, str]]) -> str:
    lines = ['version: "1.0.0"', "", "architectures:"]
    for arch in arches:
        lines.append(f"  - arch: {arch['arch']}")
        lines.append('    cross: ""')
        lines.append("    kind: fragment")
        lines.append("    build: true")
        lines.append(f"    configs: {arch['configs']}")
    lines.append("")
    lines.append("required_configs:")
    lines.append("  - CONFIG_SECURITY=y")
    lines.append("")
    lines.append("lsm_entry: airy")
    return "\n".join(lines) + "\n"


def write_repo(tmp_path: Path,
               arches: list[dict[str, str]] | None = None,
               bio_manifest: str | None = None,
               fragment: str | None = None,
               ci_text: str | None = None,
               matrix_text: str | None = None) -> tuple[Path, Path]:
    """构造一个自洽的合成仓库，返回 (repo_root, 基线清单路径)。"""
    arches = GOOD_ARCHES if arches is None else arches
    fragment = GOOD_FRAGMENT if fragment is None else fragment
    matrix_text = _matrix_text(arches) if matrix_text is None else matrix_text
    ci_text = GOOD_CI if ci_text is None else ci_text
    bio_manifest = BIO_MANIFEST if bio_manifest is None else bio_manifest

    for arch in arches:
        path = tmp_path / "kernel" / arch["configs"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fragment, encoding="utf-8")

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "ci-kernel.yml").write_text(ci_text, encoding="utf-8")

    matrix = tmp_path / "tools" / "defconfig-matrix" / "manifest.yaml"
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text(matrix_text, encoding="utf-8")

    manifest = tmp_path / "tools" / "btf-integration" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(bio_manifest, encoding="utf-8")
    return tmp_path, manifest


def _load(tmp_path: Path, **kwargs: str) -> bi.Manifest:
    repo, manifest = write_repo(tmp_path, **kwargs)
    return bi.load_manifest(manifest)


def _entries(tmp_path: Path) -> list[bi.ArchEntry]:
    manifest = bi.load_manifest(write_repo(tmp_path)[1])
    return bi.load_arches(manifest, tmp_path)


class TestManifestSchema:
    def test_valid(self, tmp_path: Path) -> None:
        assert bi.check_schema(_load(tmp_path)).passed

    def test_unknown_top_level_key(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST + "bogus: 1\n")
        result = bi.check_schema(manifest)
        assert not result.passed
        assert "未知字段" in result.details

    def test_missing_required_field(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST.replace(
            "pahole_package: dwarves", 'pahole_package: ""'))
        result = bi.check_schema(manifest)
        assert not result.passed
        assert "pahole_package" in result.details

    def test_matrix_path_must_be_yaml(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST.replace(
            "tools/defconfig-matrix/manifest.yaml",
            "tools/defconfig-matrix/manifest.yml"))
        result = bi.check_schema(manifest)
        assert not result.passed
        assert ".yaml" in result.details

    def test_illegal_bpf_config_item(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST.replace(
            "  - CONFIG_BPF=y\n", "  - BPF=y\n"))
        result = bi.check_schema(manifest)
        assert not result.passed
        assert "bpf_configs" in result.details

    def test_illegal_mutex_symbol(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST.replace(
            "  - CONFIG_DEBUG_INFO_SPLIT\n", "  - DEBUG_INFO_SPLIT\n"))
        result = bi.check_schema(manifest)
        assert not result.passed
        assert "btf_mutex_unset" in result.details

    def test_empty_btf_artifacts(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST.replace(
            "  - .BTF\n", "  -\n"))
        result = bi.check_schema(manifest)
        assert not result.passed
        assert "btf_artifacts" in result.details

    def test_workflow_path_must_be_yml(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST.replace(
            "  - .github/workflows/ci-kernel.yml\n",
            "  - .github/workflows/ci-kernel.yaml\n"))
        result = bi.check_schema(manifest)
        assert not result.passed
        assert "ci_workflows" in result.details


class TestFragmentsPresent:
    def test_present(self, tmp_path: Path) -> None:
        assert bi.check_frags(_entries(tmp_path), tmp_path / "kernel").passed

    def test_missing(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / GOOD_ARCHES[1]["configs"]).unlink()
        manifest = bi.load_manifest(manifest_path)
        entries = bi.load_arches(manifest, repo)
        result = bi.check_frags(entries, repo / "kernel")
        assert not result.passed
        assert "片段缺失" in result.details

    def test_matrix_manifest_absent(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(tmp_path)
        (repo / "tools" / "defconfig-matrix" / "manifest.yaml").unlink()
        manifest = bi.load_manifest(manifest_path)
        with pytest.raises(bi.ManifestError):
            bi.load_arches(manifest, repo)

    def test_matrix_without_build_entries(self, tmp_path: Path) -> None:
        text = _matrix_text(GOOD_ARCHES).replace("    build: true",
                                                 "    build: false")
        repo, manifest_path = write_repo(tmp_path, matrix_text=text)
        manifest = bi.load_manifest(manifest_path)
        with pytest.raises(bi.ManifestError):
            bi.load_arches(manifest, repo)


class TestBpfBaseline:
    def test_valid(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path)
        assert bi.check_bpf(_entries(tmp_path),
                            tmp_path / "kernel", manifest).passed

    def test_missing_item(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, fragment=GOOD_FRAGMENT.replace("CONFIG_BPF=y\n", ""))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_bpf(
            bi.load_arches(manifest, repo), repo / "kernel", manifest)
        assert not result.passed
        assert "CONFIG_BPF=y" in result.details


class TestBtfBaseline:
    def test_valid(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path)
        assert bi.check_btf(_entries(tmp_path),
                            tmp_path / "kernel", manifest).passed

    def test_missing_btf(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, fragment=GOOD_FRAGMENT.replace(
                "CONFIG_DEBUG_INFO_BTF=y\n", ""))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_btf(
            bi.load_arches(manifest, repo), repo / "kernel", manifest)
        assert not result.passed
        assert "CONFIG_DEBUG_INFO_BTF=y" in result.details

    def test_mutex_not_disabled(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, fragment=GOOD_FRAGMENT.replace(
                "# CONFIG_DEBUG_INFO_REDUCED is not set\n",
                "CONFIG_DEBUG_INFO_REDUCED=y\n"))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_btf(
            bi.load_arches(manifest, repo), repo / "kernel", manifest)
        assert not result.passed
        assert "DEBUG_INFO_REDUCED" in result.details

    def test_missing_dwarf_choice(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, fragment=GOOD_FRAGMENT.replace(
                "CONFIG_DEBUG_INFO_DWARF4=y\n", ""))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_btf(
            bi.load_arches(manifest, repo), repo / "kernel", manifest)
        assert not result.passed
        assert "DEBUG_INFO 形态缺失" in result.details


class TestTracingChain:
    def test_valid(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path)
        assert bi.check_trace(_entries(tmp_path),
                              tmp_path / "kernel", manifest).passed

    def test_missing_ftrace(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, fragment=GOOD_FRAGMENT.replace("CONFIG_FTRACE=y\n", ""))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_trace(
            bi.load_arches(manifest, repo), repo / "kernel", manifest)
        assert not result.passed
        assert "CONFIG_FTRACE=y" in result.details


class TestCiSupply:
    def test_valid(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path)
        assert bi.check_ci_supply(manifest, tmp_path).passed

    def test_missing_pahole(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, ci_text=GOOD_CI.replace("dwarves", "elfutils"))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_ci_supply(manifest, repo)
        assert not result.passed
        assert "dwarves" in result.details

    def test_missing_artifact_assertion(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(
            tmp_path, ci_text=GOOD_CI.replace(".BTF", "BTF_SEGMENT"))
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_ci_supply(manifest, repo)
        assert not result.passed
        assert ".BTF" in result.details

    def test_workflow_absent(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(tmp_path)
        (repo / ".github" / "workflows" / "ci-kernel.yml").unlink()
        manifest = bi.load_manifest(manifest_path)
        result = bi.check_ci_supply(manifest, repo)
        assert not result.passed
        assert "无法读取" in result.details


class TestBuildChecksAndCli:
    def test_happy_path_six_checks(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path)
        results = bi.build_checks(manifest, tmp_path, tmp_path / "kernel")
        assert len(results) == 6
        assert all(r.passed for r in results)

    def test_schema_failure_short_circuits(self, tmp_path: Path) -> None:
        manifest = _load(tmp_path, bio_manifest=BIO_MANIFEST + "bogus: 1\n")
        results = bi.build_checks(manifest, tmp_path, tmp_path / "kernel")
        assert len(results) == 1
        assert not results[0].passed

    def test_guard_failure_short_circuits(self, tmp_path: Path) -> None:
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / GOOD_ARCHES[0]["configs"]).unlink()
        manifest = bi.load_manifest(manifest_path)
        results = bi.build_checks(manifest, repo, repo / "kernel")
        assert len(results) == 2
        assert results[0].passed
        assert not results[1].passed

    def test_check_mode_reports_pass(self, tmp_path: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
        repo, _ = write_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            bi.main(["--repo-root", str(repo), "--check"])
        assert exc.value.code == bi.EXIT_PASS
        out = capsys.readouterr().out
        assert "BTF-INTEGRATION: PASS (6/6 checks)" in out

    def test_check_mode_reports_fail(self, tmp_path: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / GOOD_ARCHES[1]["configs"]).unlink()
        with pytest.raises(SystemExit) as exc:
            bi.main(["--repo-root", str(repo), "--check"])
        assert exc.value.code == bi.EXIT_FAIL
        assert "BTF-INTEGRATION: FAIL" in capsys.readouterr().out

    def test_missing_kernel_dir_exits_usage(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            bi.main(["--repo-root", str(tmp_path / "nowhere"), "--check"])
        assert exc.value.code == bi.EXIT_USAGE

    def test_missing_manifest_exits_usage(self, tmp_path: Path) -> None:
        (tmp_path / "kernel").mkdir()
        with pytest.raises(SystemExit) as exc:
            bi.main(["--repo-root", str(tmp_path), "--check"])
        assert exc.value.code == bi.EXIT_USAGE

    def test_real_repo_passes(self, repo_root: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            bi.main(["--repo-root", str(repo_root), "--check"])
        assert exc.value.code == bi.EXIT_PASS
