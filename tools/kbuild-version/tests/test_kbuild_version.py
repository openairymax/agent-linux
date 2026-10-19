# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/kbuild-version 单元测试：清单 schema / 版本文件存在性 / 四元组一致 /
OS-STD-049 / include 指令 / filechk 宏注入 / 契约快照 / 真实 version.h /
build_checks 编排 / CLI 与真实仓库端到端。

测试以 tmp_path 构造受控合成仓库，逐项断言各检查器的通过/拒绝语义；另有一例
在真实仓库上运行，作为端到端回归。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

import shutil

import pytest

import kbuild_version as kv

# ─── Synthetic repository builders ─────────────────────────────────────────

GOOD_VERSION_FILE = """\
# SPDX-License-Identifier: GPL-2.0
AIRYMAXOS_LTS     = 0
AIRYMAXOS_MAJOR   = 0
AIRYMAXOS_MINOR   = 3
AIRYMAXOS_RELEASE = ""
"""

# 顶层 Makefile 片段：include 指令 + filechk_version.h 注入块。
# 采用 raw 字符串保留 Makefile 中的 `\#` 与 `\"`。
GOOD_MAKEFILE = r"""# SPDX-License-Identifier: GPL-2.0
VERSION = 6
PATCHLEVEL = 6
SUBLEVEL = 157

include $(srctree)/Makefile.airymaxos

define filechk_version.h
	echo \#define LINUX_VERSION_MAJOR $(VERSION);                    \
	echo \#define AIRYMAXOS_LTS $(AIRYMAXOS_LTS);                    \
	echo \#define AIRYMAXOS_MAJOR $(AIRYMAXOS_MAJOR);                \
	echo \#define AIRYMAXOS_MINOR $(AIRYMAXOS_MINOR);                \
	echo '#define AIRYMAXOS_VERSION(a,b) (((a) << 8) + (b))';        \
	echo \#define AIRYMAXOS_VERSION_CODE $(shell                     \
	expr $(AIRYMAXOS_MAJOR) \* 256 + $(AIRYMAXOS_MINOR));            \
	echo \#define AIRYMAXOS_RELEASE \"$(AIRYMAXOS_RELEASE)\"
endef
"""

GOOD_MANIFEST = """\
version: "1.0.0"
airymaxos_makefile: kernel/Makefile.airymaxos
top_makefile: kernel/Makefile
contract: tools/kbuild-version/contract/version_h.airy.txt
version_tuple:
  lts: 0
  major: 0
  minor: 3
  release: ""
"""

GOOD_CONTRACT = """\
#define AIRYMAXOS_LTS 0
#define AIRYMAXOS_MAJOR 0
#define AIRYMAXOS_MINOR 3
#define AIRYMAXOS_VERSION(a,b) (((a) << 8) + (b))
#define AIRYMAXOS_VERSION_CODE 3
#define AIRYMAXOS_RELEASE ""
"""

# 真实 filechk 产物（含 Linux 原生宏 + AirymaxOS 注入宏）。
REAL_VERSION_H = (
    "#define LINUX_VERSION_CODE 394909\n"
    "#define KERNEL_VERSION(a,b,c) (((a) << 16) + ((b) << 8) + "
    "((c) > 255 ? 255 : (c)))\n"
    "#define LINUX_VERSION_MAJOR 6\n"
    "#define LINUX_VERSION_PATCHLEVEL 6\n"
    "#define LINUX_VERSION_SUBLEVEL 157\n"
    "#define AIRYMAXOS_LTS 0\n"
    "#define AIRYMAXOS_MAJOR 0\n"
    "#define AIRYMAXOS_MINOR 3\n"
    "#define AIRYMAXOS_VERSION(a,b) (((a) << 8) + (b))\n"
    "#define AIRYMAXOS_VERSION_CODE 3\n"
    '#define AIRYMAXOS_RELEASE ""\n'
)


def write_repo(tmp_path, version_file=None, top_makefile=None,
               manifest_yaml=None, contract=None):
    """构造一个自洽的合成仓库，返回 (repo_root, 清单路径)。"""
    version_file = GOOD_VERSION_FILE if version_file is None else version_file
    top_makefile = GOOD_MAKEFILE if top_makefile is None else top_makefile
    manifest_yaml = GOOD_MANIFEST if manifest_yaml is None else manifest_yaml
    contract = GOOD_CONTRACT if contract is None else contract

    kernel = tmp_path / "kernel"
    kernel.mkdir(parents=True, exist_ok=True)
    (kernel / "Makefile.airymaxos").write_text(version_file, encoding="utf-8")
    (kernel / "Makefile").write_text(top_makefile, encoding="utf-8")

    tool = tmp_path / "tools" / "kbuild-version"
    (tool / "contract").mkdir(parents=True, exist_ok=True)
    (tool / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")
    (tool / "contract" / "version_h.airy.txt").write_text(contract,
                                                          encoding="utf-8")
    return tmp_path, tool / "manifest.yaml"


def _load(tmp_path, **kwargs):
    _, manifest = write_repo(tmp_path, **kwargs)
    return kv.load_manifest(manifest)


def _assignments(tmp_path, version_file=None):
    write_repo(tmp_path, version_file=version_file)
    text = (tmp_path / "kernel" / "Makefile.airymaxos").read_text(
        encoding="utf-8")
    return kv.parse_vars(text)


def _write_vh(tmp_path, text=REAL_VERSION_H):
    path = tmp_path / "generated" / "version.h"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ─── Helpers ───────────────────────────────────────────────────────────────


class TestHelpers:
    def test_parse_vars_skips_comments_and_blanks(self):
        text = "# comment\n\nAIRYMAXOS_LTS = 0\n  AIRYMAXOS_MAJOR = 0\n"
        assert kv.parse_vars(text) == [("AIRYMAXOS_LTS", "0"),
                                       ("AIRYMAXOS_MAJOR", "0")]

    def test_expect_vars_quotes_release(self):
        expected = kv.expect_vars(
            {"lts": 0, "major": 0, "minor": 3, "release": ""})
        assert expected == {
            "AIRYMAXOS_LTS": "0",
            "AIRYMAXOS_MAJOR": "0",
            "AIRYMAXOS_MINOR": "3",
            "AIRYMAXOS_RELEASE": '""',
        }

    def test_render_contract_derives_version_code(self):
        rendered = kv.render_contract(
            {"lts": 1, "major": 2, "minor": 4, "release": "rc1"})
        assert "#define AIRYMAXOS_VERSION_CODE 516" in rendered
        assert '#define AIRYMAXOS_RELEASE "rc1"' in rendered

    def test_filechk_block_absent(self):
        assert kv.filechk_block("VERSION = 6\n") is None

    def test_find_repo_root_points_at_repo(self, repo_root):
        assert kv.find_repo_root() == repo_root
        assert (kv.find_repo_root() / "kernel" / "Makefile").is_file()


# ─── 清单 schema ───────────────────────────────────────────────────────────


class TestManifestSchema:
    def test_valid(self, tmp_path):
        assert kv.check_schema(_load(tmp_path)).passed

    def test_unknown_top_level_key(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST + "bogus: 1\n")
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "未知字段" in result.details

    def test_missing_required_field(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            "top_makefile: kernel/Makefile\n", 'top_makefile: ""\n'))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "top_makefile" in result.details

    def test_airymaxos_makefile_suffix(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            "kernel/Makefile.airymaxos", "kernel/Makefile.vendor"))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert ".airymaxos" in result.details

    def test_contract_suffix(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            "tools/kbuild-version/contract/version_h.airy.txt",
            "tools/kbuild-version/contract/version_h.airy.md"))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert ".txt" in result.details

    def test_tuple_out_of_range(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            "  minor: 3\n", "  minor: 256\n"))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "version_tuple.minor" in result.details

    def test_tuple_bool_rejected(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            "  major: 0\n", "  major: true\n"))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "version_tuple.major" in result.details

    def test_tuple_missing_key(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            '  release: ""\n', ""))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "version_tuple 缺少字段" in result.details

    def test_tuple_release_must_be_str(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            '  release: ""', "  release: 1"))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "version_tuple.release" in result.details

    def test_tuple_not_mapping(self, tmp_path):
        manifest = _load(tmp_path, manifest_yaml=GOOD_MANIFEST.replace(
            'version_tuple:\n  lts: 0\n  major: 0\n'
            '  minor: 3\n  release: ""\n',
            'version_tuple: "oops"\n'))
        result = kv.check_schema(manifest)
        assert not result.passed
        assert "必须是映射" in result.details


# ─── 版本文件存在性（guard） ───────────────────────────────────────────────


class TestVersionFilePresent:
    def test_present(self, tmp_path):
        repo, _ = write_repo(tmp_path)
        manifest = kv.load_manifest(repo / "tools" / "kbuild-version"
                                    / "manifest.yaml")
        assert kv.check_vfile(manifest, repo).passed

    def test_missing(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / "Makefile.airymaxos").unlink()
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_vfile(manifest, repo)
        assert not result.passed
        assert "版本文件缺失" in result.details


# ─── 四元组一致 ───────────────────────────────────────────────────────────


class TestTupleMatches:
    def test_valid(self, tmp_path):
        assert kv.check_tuple(_assignments(tmp_path),
                              _load(tmp_path)).passed

    def test_mismatch(self, tmp_path):
        version_file = GOOD_VERSION_FILE.replace("AIRYMAXOS_MINOR   = 3",
                                                 "AIRYMAXOS_MINOR   = 9")
        result = kv.check_tuple(_assignments(tmp_path, version_file),
                                _load(tmp_path))
        assert not result.passed
        assert "AIRYMAXOS_MINOR" in result.details

    def test_missing_var(self, tmp_path):
        version_file = GOOD_VERSION_FILE.replace(
            'AIRYMAXOS_RELEASE = ""\n', "")
        result = kv.check_tuple(_assignments(tmp_path, version_file),
                                _load(tmp_path))
        assert not result.passed
        assert "AIRYMAXOS_RELEASE" in result.details
        assert "<缺失>" in result.details


# ─── OS-STD-049 ───────────────────────────────────────────────────────────


class TestOnlyVersionVars:
    def test_valid(self, tmp_path):
        assert kv.check_vars(_assignments(tmp_path)).passed

    def test_extra_var(self, tmp_path):
        version_file = GOOD_VERSION_FILE + "AIRYMAXOS_FEATURE = 1\n"
        result = kv.check_vars(_assignments(tmp_path, version_file))
        assert not result.passed
        assert "AIRYMAXOS_FEATURE" in result.details


# ─── include 指令 ─────────────────────────────────────────────────────────


class TestIncludeDirective:
    def test_valid(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        manifest = kv.load_manifest(manifest_path)
        assert kv.check_include(manifest, repo).passed

    def test_missing_directive(self, tmp_path):
        top = GOOD_MAKEFILE.replace(kv.INCLUDE_DIRECTIVE + "\n", "")
        repo, manifest_path = write_repo(tmp_path, top_makefile=top)
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_include(manifest, repo)
        assert not result.passed
        assert kv.INCLUDE_DIRECTIVE in result.details

    def test_top_makefile_absent(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / "Makefile").unlink()
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_include(manifest, repo)
        assert not result.passed
        assert "顶层 Makefile 缺失" in result.details


# ─── filechk 宏注入 ───────────────────────────────────────────────────────


class TestFilechkMacros:
    def test_valid(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        manifest = kv.load_manifest(manifest_path)
        assert kv.check_macros(manifest, repo).passed

    def test_missing_macro(self, tmp_path):
        top = GOOD_MAKEFILE.replace("AIRYMAXOS_VERSION_CODE",
                                    "AIRYMAXOS_VCODE")
        repo, manifest_path = write_repo(tmp_path, top_makefile=top)
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_macros(manifest, repo)
        assert not result.passed
        assert "AIRYMAXOS_VERSION_CODE" in result.details

    def test_no_block(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path,
                                         top_makefile="VERSION = 6\n")
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_macros(manifest, repo)
        assert not result.passed
        assert "未找到" in result.details

    def test_top_makefile_absent(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / "Makefile").unlink()
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_macros(manifest, repo)
        assert not result.passed
        assert "顶层 Makefile 缺失" in result.details


# ─── 契约快照 ─────────────────────────────────────────────────────────────


class TestContract:
    def test_valid(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        manifest = kv.load_manifest(manifest_path)
        assert kv.check_contract(manifest, repo).passed

    def test_missing_contract_file(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        (repo / "tools" / "kbuild-version" / "contract"
         / "version_h.airy.txt").unlink()
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_contract(manifest, repo)
        assert not result.passed
        assert "契约文件缺失" in result.details

    def test_mismatch(self, tmp_path):
        contract = GOOD_CONTRACT.replace(
            "#define AIRYMAXOS_VERSION_CODE 3",
            "#define AIRYMAXOS_VERSION_CODE 4")
        repo, manifest_path = write_repo(tmp_path, contract=contract)
        manifest = kv.load_manifest(manifest_path)
        result = kv.check_contract(manifest, repo)
        assert not result.passed
        assert "AIRYMAXOS_VERSION_CODE" in result.details


# ─── 真实 version.h ───────────────────────────────────────────────────────


class TestRealVersionH:
    def test_valid(self, tmp_path):
        manifest = _load(tmp_path)
        version_h = _write_vh(tmp_path)
        assert kv.check_version_h(manifest, version_h).passed

    def test_absent(self, tmp_path):
        manifest = _load(tmp_path)
        result = kv.check_version_h(manifest, tmp_path / "none" / "v.h")
        assert not result.passed
        assert "version.h 不存在" in result.details

    def test_mismatch(self, tmp_path):
        manifest = _load(tmp_path)
        text = REAL_VERSION_H.replace("#define AIRYMAXOS_MINOR 3",
                                      "#define AIRYMAXOS_MINOR 5")
        result = kv.check_version_h(manifest,
                                    _write_vh(tmp_path, text))
        assert not result.passed
        assert "AIRYMAXOS_MINOR 5" in result.details


# ─── build_checks 编排 / CLI ──────────────────────────────────────────────


class TestBuildChecksAndCli:
    def test_happy_path_seven_checks(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        manifest = kv.load_manifest(manifest_path)
        results = kv.build_checks(manifest, repo)
        assert len(results) == 7
        assert all(r.passed for r in results)

    def test_version_h_appends_eighth(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        manifest = kv.load_manifest(manifest_path)
        results = kv.build_checks(manifest, repo,
                                  version_h=_write_vh(tmp_path))
        assert len(results) == 8
        assert all(r.passed for r in results)

    def test_schema_failure_short_circuits(self, tmp_path):
        repo, manifest_path = write_repo(
            tmp_path, manifest_yaml=GOOD_MANIFEST + "bogus: 1\n")
        manifest = kv.load_manifest(manifest_path)
        results = kv.build_checks(manifest, repo)
        assert len(results) == 1
        assert not results[0].passed

    def test_guard_failure_short_circuits(self, tmp_path):
        repo, manifest_path = write_repo(tmp_path)
        (repo / "kernel" / "Makefile.airymaxos").unlink()
        manifest = kv.load_manifest(manifest_path)
        results = kv.build_checks(manifest, repo)
        assert len(results) == 2
        assert results[0].passed
        assert not results[1].passed

    def test_check_mode_reports_pass(self, tmp_path, capsys):
        repo, _ = write_repo(tmp_path)
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(repo), "--check"])
        assert exc.value.code == kv.EXIT_PASS
        assert "KBUILD-VERSION: PASS (7/7 checks)" in capsys.readouterr().out

    def test_check_mode_reports_fail(self, tmp_path, capsys):
        repo, manifest_path = write_repo(
            tmp_path, manifest_yaml=GOOD_MANIFEST + "bogus: 1\n")
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(repo), "--check"])
        assert exc.value.code == kv.EXIT_FAIL
        assert "KBUILD-VERSION: FAIL" in capsys.readouterr().out

    def test_version_h_flag_via_main(self, tmp_path, capsys):
        repo, _ = write_repo(tmp_path)
        version_h = _write_vh(tmp_path)
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(repo), "--version-h", str(version_h),
                     "--check"])
        assert exc.value.code == kv.EXIT_PASS
        assert "KBUILD-VERSION: PASS (8/8 checks)" in capsys.readouterr().out

    def test_missing_repo_root_exits_usage(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(tmp_path / "nowhere"), "--check"])
        assert exc.value.code == kv.EXIT_USAGE

    def test_missing_manifest_exits_usage(self, tmp_path):
        (tmp_path / "kernel").mkdir()
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(tmp_path), "--check"])
        assert exc.value.code == kv.EXIT_USAGE

    def test_missing_kernel_dir_exits_usage(self, tmp_path):
        repo, _ = write_repo(tmp_path)
        shutil.rmtree(repo / "kernel")
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(repo), "--check"])
        assert exc.value.code == kv.EXIT_USAGE

    def test_real_repo_passes(self, repo_root):
        with pytest.raises(SystemExit) as exc:
            kv.main(["--repo-root", str(repo_root), "--check"])
        assert exc.value.code == kv.EXIT_PASS
