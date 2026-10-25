# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/defconfig-matrix 单元测试：清单 schema / 清单↔文件系统一致性 /
defconfig 格式 / 必备 CONFIG / LSM 入口 / CI 矩阵与必备清单同步 /
Kconfig 声明闭合 / CLI 与真实仓库端到端。

测试以 tmp_path 构造受控合成仓库，逐项断言各检查器的通过/拒绝语义；
另有一例在真实仓库上运行，作为端到端回归。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

from pathlib import Path

import pytest

import defconfig_matrix as dm

# ─── Synthetic repository builders ─────────────────────────────────────────

GOOD_ARCHES = [
    {"arch": "x86_64", "cross": "", "kind": "fragment",
     "build": True, "configs": "arch/x86/configs/airy_defconfig"},
    {"arch": "arm64", "cross": "aarch64-linux-gnu-", "kind": "fragment",
     "build": True, "configs": "arch/arm64/configs/airy_defconfig"},
]

DEFAULT_REQUIRED = [
    "CONFIG_SECURITY=y",
    "CONFIG_SECURITY_AIRY=y",
    "CONFIG_IO_URING=y",
]

GOOD_LSM = "landlock,airy,selinux"


def _kdir(configs: str) -> str:
    return configs.split("/")[1]


def _defconfig_text(lsm_list: str = GOOD_LSM, extra: str = "") -> str:
    return (
        "# SPDX-License-Identifier: GPL-2.0-only\n"
        "CONFIG_SECURITY=y\n"
        "CONFIG_SECURITY_AIRY=y\n"
        "CONFIG_IO_URING=y\n"
        f'CONFIG_LSM="{lsm_list}"\n'
        f"{extra}"
    )


def _manifest_text(arches, required, lsm_entry="airy", version="1.0.0") -> str:
    lines = [f'version: "{version}"', "", "architectures:"]
    for arch in arches:
        lines.append(f"  - arch: {arch['arch']}")
        lines.append(f'    cross: "{arch["cross"]}"')
        lines.append(f"    kind: {arch['kind']}")
        lines.append(f"    build: {str(arch['build']).lower()}")
        lines.append(f"    configs: {arch['configs']}")
    lines.append("")
    lines.append("required_configs:")
    for item in required:
        lines.append(f"  - {item}")
    lines.append("")
    lines.append(f"lsm_entry: {lsm_entry}")
    return "\n".join(lines) + "\n"


def _ci_airy_text(arches, required) -> str:
    include = "".join(
        f"          - arch: {arch['arch']}\n"
        f"            kdir: {_kdir(arch['configs'])}\n"
        f'            cross: "{arch["cross"]}"\n'
        for arch in arches)
    req = "".join(f"            '{item}'\n" for item in required)
    return (
        "jobs:\n"
        "  airy-build:\n"
        "    strategy:\n"
        "      matrix:\n"
        "        include:\n"
        f"{include}"
        "  airy-static:\n"
        "    steps:\n"
        "      - name: verify\n"
        "        run: |\n"
        "          required=(\n"
        f"{req}"
        "          )\n"
    )


def _ci_kernel_text(arches) -> str:
    arch_list = ", ".join(arch["arch"] for arch in arches)
    return (
        "jobs:\n"
        "  kernel-build:\n"
        "    strategy:\n"
        "      matrix:\n"
        f"        arch: [{arch_list}]\n"
        "  kernel-verify:\n"
        "    steps:\n"
        "      - name: verify\n"
        "        run: ci/ci.sh kernel-refbuild\n"
    )


def _kernel_gate_text(required) -> str:
    req = "".join(f"    '{item}'\n" for item in required)
    return (
        "#!/usr/bin/env bash\n"
        "readonly REQUIRED_CONF=(\n"
        f"{req}"
        ")\n"
    )


def write_repo(tmp_path, arches=None, required=None,
               lsm_entry="airy", lsm_list=GOOD_LSM):
    """构造一个自洽的合成仓库，返回清单路径。"""
    arches = GOOD_ARCHES if arches is None else arches
    required = DEFAULT_REQUIRED if required is None else required
    kernel = tmp_path / "kernel"
    for arch in arches:
        path = kernel / arch["configs"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_defconfig_text(lsm_list), encoding="utf-8")

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    build_arches = [arch for arch in arches if arch["build"]]
    (workflows / "ci-airy.yml").write_text(
        _ci_airy_text(build_arches, required), encoding="utf-8")
    (workflows / "ci-kernel.yml").write_text(
        _ci_kernel_text(arches), encoding="utf-8")

    gates = tmp_path / "ci" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "kernel-refbuild.sh").write_text(
        _kernel_gate_text(required), encoding="utf-8")

    manifest = tmp_path / "tools" / "defconfig-matrix" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        _manifest_text(arches, required, lsm_entry), encoding="utf-8")
    return manifest


def _defconfig(tmp_path, arch) -> Path:
    return tmp_path / "kernel" / arch["configs"]


# ─── 清单 schema ───────────────────────────────────────────────────────────


class TestManifestSchema:
    def test_valid(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_manifest_schema(manifest).passed

    def test_build_true_requires_fragment(self, tmp_path):
        arches = [dict(GOOD_ARCHES[0], kind="full")]
        manifest = dm.load_manifest(write_repo(tmp_path, arches=arches))
        result = dm.check_manifest_schema(manifest)
        assert not result.passed
        assert "kind=fragment" in result.details

    def test_duplicate_arch(self, tmp_path):
        arches = [GOOD_ARCHES[0], dict(GOOD_ARCHES[1], arch="x86_64")]
        manifest = dm.load_manifest(write_repo(tmp_path, arches=arches))
        result = dm.check_manifest_schema(manifest)
        assert not result.passed
        assert "重复" in result.details

    def test_unknown_top_level_key(self, tmp_path):
        path = write_repo(tmp_path)
        path.write_text(path.read_text(encoding="utf-8") + "bogus: 1\n",
                        encoding="utf-8")
        result = dm.check_manifest_schema(dm.load_manifest(path))
        assert not result.passed
        assert "未知字段" in result.details

    def test_unknown_arch_key(self, tmp_path):
        path = write_repo(tmp_path)
        text = path.read_text(encoding="utf-8").replace(
            "    configs: arch/x86/configs/airy_defconfig",
            "    oops: 1\n    configs: arch/x86/configs/airy_defconfig")
        path.write_text(text, encoding="utf-8")
        result = dm.check_manifest_schema(dm.load_manifest(path))
        assert not result.passed
        assert "未知字段" in result.details

    def test_missing_required_field(self, tmp_path):
        path = write_repo(tmp_path)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "lsm_entry: airy", 'lsm_entry: ""'),
            encoding="utf-8")
        result = dm.check_manifest_schema(dm.load_manifest(path))
        assert not result.passed
        assert "lsm_entry" in result.details

    def test_required_configs_must_be_config(self, tmp_path):
        manifest = dm.load_manifest(
            write_repo(tmp_path, required=["SECURITY=y"]))
        result = dm.check_manifest_schema(manifest)
        assert not result.passed
        assert "CONFIG_" in result.details


# ─── 清单 ↔ 文件系统 ───────────────────────────────────────────────────────


class TestManifestFs:
    def test_match(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_manifest_fs(manifest, tmp_path / "kernel").passed

    def test_registered_but_missing(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        _defconfig(tmp_path, GOOD_ARCHES[1]).unlink()
        result = dm.check_manifest_fs(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "文件缺失" in result.details

    def test_on_disk_but_unregistered(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        extra = tmp_path / "kernel" / "arch" / "riscv" / "configs"
        extra.mkdir(parents=True)
        (extra / "airy_defconfig").write_text(
            _defconfig_text(), encoding="utf-8")
        result = dm.check_manifest_fs(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "未登记" in result.details


# ─── defconfig 格式 ────────────────────────────────────────────────────────


class TestDefconfigFormat:
    def test_valid(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_defconfig_format(manifest, tmp_path / "kernel").passed

    def test_missing_spdx(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        _defconfig(tmp_path, GOOD_ARCHES[0]).write_text(
            _defconfig_text().split("\n", 1)[1], encoding="utf-8")
        result = dm.check_defconfig_format(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "SPDX" in result.details

    def test_duplicate_key(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        _defconfig(tmp_path, GOOD_ARCHES[0]).write_text(
            _defconfig_text(extra="CONFIG_SECURITY=y\n"), encoding="utf-8")
        result = dm.check_defconfig_format(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "重复配置键" in result.details

    def test_illegal_line(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        _defconfig(tmp_path, GOOD_ARCHES[0]).write_text(
            _defconfig_text(extra="this is not a config line\n"),
            encoding="utf-8")
        result = dm.check_defconfig_format(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "非法行" in result.details


# ─── 必备 CONFIG ───────────────────────────────────────────────────────────


class TestRequiredConfigs:
    def test_valid(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_required_configs(manifest, tmp_path / "kernel").passed

    def test_missing_item(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        _defconfig(tmp_path, GOOD_ARCHES[1]).write_text(
            _defconfig_text().replace("CONFIG_IO_URING=y\n", ""),
            encoding="utf-8")
        result = dm.check_required_configs(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "CONFIG_IO_URING=y" in result.details


# ─── LSM 入口 ──────────────────────────────────────────────────────────────


class TestLsmEntry:
    def test_valid(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_lsm_entry(manifest, tmp_path / "kernel").passed

    def test_not_present(self, tmp_path):
        manifest = dm.load_manifest(
            write_repo(tmp_path, lsm_list="landlock,selinux"))
        result = dm.check_lsm_entry(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "airy" in result.details

    def test_config_lsm_absent(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        _defconfig(tmp_path, GOOD_ARCHES[0]).write_text(
            _defconfig_text().replace(f'CONFIG_LSM="{GOOD_LSM}"\n', ""),
            encoding="utf-8")
        result = dm.check_lsm_entry(manifest, tmp_path / "kernel")
        assert not result.passed
        assert "未设置 CONFIG_LSM" in result.details


# ─── CI 构建矩阵同步 ───────────────────────────────────────────────────────


class TestCiMatrixSync:
    def test_valid(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_ci_matrix_sync(manifest, tmp_path).passed

    def test_kdir_drift(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-airy.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "kdir: x86\n", "kdir: x86_64\n"), encoding="utf-8")
        result = dm.check_ci_matrix_sync(manifest, tmp_path)
        assert not result.passed
        assert "kdir 漂移" in result.details

    def test_cross_drift(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-airy.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            'cross: "aarch64-linux-gnu-"', 'cross: "aarch64-unknown-linux-"'),
            encoding="utf-8")
        result = dm.check_ci_matrix_sync(manifest, tmp_path)
        assert not result.passed
        assert "cross 漂移" in result.details

    def test_missing_build_arch(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-airy.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "          - arch: arm64\n"
            "            kdir: arm64\n"
            '            cross: "aarch64-linux-gnu-"\n', ""),
            encoding="utf-8")
        result = dm.check_ci_matrix_sync(manifest, tmp_path)
        assert not result.passed
        assert "缺少清单 build=true 架构" in result.details

    def test_extra_build_arch(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-airy.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "  airy-static:",
            "          - arch: riscv\n"
            "            kdir: riscv\n"
            '            cross: "riscv64-linux-gnu-"\n'
            "  airy-static:"), encoding="utf-8")
        result = dm.check_ci_matrix_sync(manifest, tmp_path)
        assert not result.passed
        assert "未标 build=true 的架构" in result.details

    def test_unknown_arch_in_ci_kernel(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-kernel.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "arch: [x86_64, arm64]", "arch: [x86_64, arm64, riscv]"),
            encoding="utf-8")
        result = dm.check_ci_matrix_sync(manifest, tmp_path)
        assert not result.passed
        assert "未登记架构" in result.details


# ─── CI 必备 CONFIG 清单同步 ───────────────────────────────────────────────


class TestCiRequiredSync:
    def test_valid(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        assert dm.check_ci_required_sync(manifest, tmp_path).passed

    def test_missing_item(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-airy.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "            'CONFIG_IO_URING=y'\n", ""), encoding="utf-8")
        result = dm.check_ci_required_sync(manifest, tmp_path)
        assert not result.passed
        assert "缺少清单项" in result.details

    def test_extra_item(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / "ci" / "gates" / "kernel-refbuild.sh"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "    'CONFIG_SECURITY=y'\n",
            "    'CONFIG_SECURITY=y'\n"
            "    'CONFIG_EXTRA=y'\n"), encoding="utf-8")
        result = dm.check_ci_required_sync(manifest, tmp_path)
        assert not result.passed
        assert "清单外项" in result.details

    def test_missing_kernel_gate(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        (tmp_path / "ci" / "gates" / "kernel-refbuild.sh").unlink()
        result = dm.check_ci_required_sync(manifest, tmp_path)
        assert not result.passed
        assert "kernel-refbuild.sh" in result.details

    def test_missing_block(self, tmp_path):
        manifest = dm.load_manifest(write_repo(tmp_path))
        path = tmp_path / ".github" / "workflows" / "ci-airy.yml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:text.index("          required=(")],
                        encoding="utf-8")
        result = dm.check_ci_required_sync(manifest, tmp_path)
        assert not result.passed
        assert "未找到 required=" in result.details


# ─── Kconfig 声明闭合 ──────────────────────────────────────────────────────


class TestKconfigDeclared:
    def _write_airy_kbuild(self, tmp_path, makefile: str, kconfig: str) -> None:
        airy = tmp_path / "kernel" / "security" / "airy"
        airy.mkdir(parents=True, exist_ok=True)
        (airy / "Makefile").write_text(makefile, encoding="utf-8")
        (airy / "Kconfig").write_text(kconfig, encoding="utf-8")

    def test_no_reference_passes(self, tmp_path):
        write_repo(tmp_path)
        result = dm.check_kconfig_declared(tmp_path / "kernel")
        assert result.passed

    def test_declared_reference_passes(self, tmp_path):
        write_repo(tmp_path)
        self._write_airy_kbuild(
            tmp_path,
            "obj-$(CONFIG_SECURITY_AIRY) += airy.o\n",
            "config SECURITY_AIRY\n\tbool \"Airymax LSM\"\n")
        assert dm.check_kconfig_declared(tmp_path / "kernel").passed

    def test_undeclared_reference_fails(self, tmp_path):
        write_repo(tmp_path)
        self._write_airy_kbuild(
            tmp_path,
            "obj-$(CONFIG_AIRY_NOPE) += nope.o\n",
            "config SECURITY_AIRY\n\tbool \"Airymax LSM\"\n")
        result = dm.check_kconfig_declared(tmp_path / "kernel")
        assert not result.passed
        assert "CONFIG_AIRY_NOPE" in result.details


# ─── build_checks / CLI ────────────────────────────────────────────────────


class TestBuildChecksAndCli:
    def test_schema_failure_short_circuits(self, tmp_path):
        arches = [dict(GOOD_ARCHES[0], kind="full")]
        manifest = dm.load_manifest(write_repo(tmp_path, arches=arches))
        results = dm.build_checks(manifest, tmp_path, tmp_path / "kernel")
        assert len(results) == 1
        assert not results[0].passed

    def test_check_mode_reports_pass(self, tmp_path, capsys):
        write_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            dm.main(["--repo-root", str(tmp_path), "--check"])
        assert exc.value.code == dm.EXIT_PASS
        assert "DEFCONFIG-MATRIX: PASS" in capsys.readouterr().out

    def test_check_mode_reports_fail(self, tmp_path, capsys):
        manifest_path = write_repo(tmp_path)
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                "lsm_entry: airy", 'lsm_entry: ""'), encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            dm.main(["--repo-root", str(tmp_path), "--check"])
        assert exc.value.code == dm.EXIT_FAIL
        assert "DEFCONFIG-MATRIX: FAIL" in capsys.readouterr().out

    def test_missing_kernel_dir_exits_usage(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            dm.main(["--repo-root", str(tmp_path / "nowhere"), "--check"])
        assert exc.value.code == dm.EXIT_USAGE

    def test_missing_manifest_exits_usage(self, tmp_path):
        (tmp_path / "kernel").mkdir()
        with pytest.raises(SystemExit) as exc:
            dm.main(["--repo-root", str(tmp_path), "--check"])
        assert exc.value.code == dm.EXIT_USAGE

    def test_real_repo_passes(self, repo_root):
        with pytest.raises(SystemExit) as exc:
            dm.main(["--repo-root", str(repo_root), "--check"])
        assert exc.value.code == dm.EXIT_PASS
