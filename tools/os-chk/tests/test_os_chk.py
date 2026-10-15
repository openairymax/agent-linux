# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/os-chk 单元测试：注册表解析 / schema 校验 / 门禁调度 / 门禁判定 /
报告格式 / CLI / 真实仓库端到端。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import os_chk as oc

# ─── Fixtures / helpers ────────────────────────────────────────────────────

# 执行器驱动 → 脚本扩展名。注册表新增驱动时需在此登记，否则
# TestShippedRegistry.test_all_executors_exist 会失败，以防执行器指向
# 不存在的脚本却在本机静默通过。
EXECUTOR_SUFFIX = {
    "python3": ".py",
    "perl": ".pl",
}

GOOD_REGISTRY = """
version: "9.9.9"
gates:
  - id: OS-CHK-DOC-01
    name: Copyright 头部
    severity: P0
    category: doc
    authority: 05-development-process.md §6.1
    executor:
      argv: ["python3", "-c", "print('ok')"]
  - id: OS-CHK-DOC-05
    name: 链接完整性
    severity: P1
    timeout_s: 30
    executor:
      argv: ["python3", "-c", "print('link ok')"]
  - id: OS-CHK-DOC-08
    name: 五维原则引用
    severity: P2
    executor:
      argv: ["python3", "-c", "print('ok')"]
"""


def make_gate(gid: str = "G-1", severity: str = "P0",
              argv=("python3", "-c", "print('ok')"),
              timeout_s=None) -> oc.Gate:
    return oc.Gate(id=gid, name="t", severity=severity, category="doc",
                   authority="x", argv=tuple(argv), timeout_s=timeout_s)


def make_result(gate: oc.Gate, status: str,
                returncode: int = 0) -> oc.GateResult:
    return oc.GateResult(gate, status, returncode, "", "", 0.0, "d")


# ─── parse_registry ────────────────────────────────────────────────────────


class TestParseRegistry:
    def test_valid_registry(self):
        version, gates = oc.parse_registry(GOOD_REGISTRY)
        assert version == "9.9.9"
        assert [g.id for g in gates] == [
            "OS-CHK-DOC-01", "OS-CHK-DOC-05", "OS-CHK-DOC-08"]
        assert gates[0].severity == "P0"
        assert gates[0].argv == ("python3", "-c", "print('ok')")
        assert gates[0].timeout_s is None
        assert gates[1].timeout_s == 30
        assert gates[2].category == ""

    def test_yaml_syntax_error(self):
        with pytest.raises(oc.RegistryError):
            oc.parse_registry("version: [unclosed\n")

    def test_root_not_mapping(self):
        with pytest.raises(oc.RegistryError, match="根节点"):
            oc.parse_registry("- a\n- b\n")

    def test_missing_version(self):
        with pytest.raises(oc.RegistryError, match="version"):
            oc.parse_registry("gates: []\n")

    def test_gates_not_list(self):
        with pytest.raises(oc.RegistryError, match="gates"):
            oc.parse_registry('version: "1"\ngates: {}\n')

    def test_gates_empty(self):
        with pytest.raises(oc.RegistryError, match="gates"):
            oc.parse_registry('version: "1"\ngates: []\n')

    def test_gate_not_mapping(self):
        with pytest.raises(oc.RegistryError, match=r"gates\[0\]"):
            oc.parse_registry('version: "1"\ngates:\n  - "x"\n')

    def test_duplicate_id(self):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    name: n\n    severity: P0\n'
                '    executor: {argv: ["python3"]}\n'
                '  - id: A\n    name: n\n    severity: P0\n'
                '    executor: {argv: ["python3"]}\n')
        with pytest.raises(oc.RegistryError, match="重复"):
            oc.parse_registry(text)

    @pytest.mark.parametrize("severity", ["P3", "p0", "", "CRITICAL"])
    def test_bad_severity(self, severity):
        text = ('version: "1"\n'
                'gates:\n'
                f'  - id: A\n    name: n\n    severity: "{severity}"\n'
                '    executor: {argv: ["python3"]}\n')
        with pytest.raises(oc.RegistryError, match="severity"):
            oc.parse_registry(text)

    def test_missing_name(self):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    severity: P0\n'
                '    executor: {argv: ["python3"]}\n')
        with pytest.raises(oc.RegistryError, match="name"):
            oc.parse_registry(text)

    def test_missing_executor(self):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    name: n\n    severity: P0\n')
        with pytest.raises(oc.RegistryError, match="executor"):
            oc.parse_registry(text)

    @pytest.mark.parametrize("argv", ['[]', '"python3"', '[""]', '[1, 2]'])
    def test_bad_argv(self, argv):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    name: n\n    severity: P0\n'
                f'    executor: {{argv: {argv}}}\n')
        with pytest.raises(oc.RegistryError, match="argv"):
            oc.parse_registry(text)

    @pytest.mark.parametrize("timeout", ["0", "-5", '"30"', "true"])
    def test_bad_timeout(self, timeout):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    name: n\n    severity: P0\n'
                f'    timeout_s: {timeout}\n'
                '    executor: {argv: ["python3"]}\n')
        with pytest.raises(oc.RegistryError, match="timeout_s"):
            oc.parse_registry(text)

    def test_unknown_gate_key(self):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    name: n\n    severity: P0\n'
                '    sevrity: P1\n'
                '    executor: {argv: ["python3"]}\n')
        with pytest.raises(oc.RegistryError, match="未知字段"):
            oc.parse_registry(text)

    def test_unknown_executor_key(self):
        text = ('version: "1"\n'
                'gates:\n'
                '  - id: A\n    name: n\n    severity: P0\n'
                '    executor: {argv: ["python3"], timeout_s: 5}\n')
        with pytest.raises(oc.RegistryError, match="executor 未知字段"):
            oc.parse_registry(text)


# ─── find_repo_root ────────────────────────────────────────────────────────


class TestFindRepoRoot:
    def test_detects_git_root(self, repo_root):
        found = oc.find_repo_root(repo_root / "tools" / "os-chk")
        assert (found / ".git").exists()
        assert found == repo_root

    def test_fallback_without_git(self, tmp_path):
        found = oc.find_repo_root(tmp_path)
        assert found == Path(oc.__file__).resolve().parents[2]


# ─── run_gate ──────────────────────────────────────────────────────────────


class TestRunGate:
    def test_pass_on_zero_exit(self, tmp_path):
        gate = make_gate(argv=("python3", "-c", "print('all good')"))
        res = oc.run_gate(gate, tmp_path)
        assert res.status == oc.STATUS_PASS
        assert res.returncode == 0
        assert res.detail == "all good"
        assert res.duration_s >= 0

    def test_fail_on_nonzero_exit(self, tmp_path):
        gate = make_gate(argv=("python3", "-c",
                               "import sys; print('boom'); sys.exit(3)"))
        res = oc.run_gate(gate, tmp_path)
        assert res.status == oc.STATUS_FAIL
        assert res.returncode == 3
        assert res.detail == "boom"

    def test_error_when_executor_missing(self, tmp_path):
        gate = make_gate(argv=("no-such-executable-xyz",))
        res = oc.run_gate(gate, tmp_path)
        assert res.status == oc.STATUS_ERROR
        assert res.returncode == 127
        assert "未找到" in res.detail

    def test_error_on_timeout(self, tmp_path):
        gate = make_gate(
            argv=("python3", "-c", "import time; time.sleep(5)"),
            timeout_s=1)
        res = oc.run_gate(gate, tmp_path)
        assert res.status == oc.STATUS_ERROR
        assert res.returncode == 124
        assert "超时" in res.detail

    def test_default_timeout_applied(self, tmp_path):
        gate = make_gate(argv=("python3", "-c", "print('ok')"))
        res = oc.run_gate(gate, tmp_path, default_timeout=42)
        assert res.status == oc.STATUS_PASS

    def test_runs_in_repo_root_cwd(self, tmp_path):
        gate = make_gate(argv=("python3", "-c",
                               "import os; print(os.getcwd())"))
        res = oc.run_gate(gate, tmp_path)
        assert res.detail == str(tmp_path)

    def test_detail_from_stderr_when_stdout_empty(self, tmp_path):
        gate = make_gate(argv=("python3", "-c",
                               "import sys; sys.stderr.write('err line\\n')"))
        res = oc.run_gate(gate, tmp_path)
        assert res.detail == "err line"

    def test_detail_fallback_when_no_output(self, tmp_path):
        gate = make_gate(argv=("python3", "-c", "pass"))
        res = oc.run_gate(gate, tmp_path)
        assert res.detail == "(无输出)"

    def test_detail_truncated(self, tmp_path):
        gate = make_gate(argv=("python3", "-c",
                               "print('x' * 500)"))
        res = oc.run_gate(gate, tmp_path)
        assert len(res.detail) == oc.DETAIL_LIMIT
        assert res.detail.endswith("…")


# ─── evaluate ──────────────────────────────────────────────────────────────


class TestEvaluate:
    def _results(self):
        return [
            make_result(make_gate("P0-FAIL", "P0"), oc.STATUS_FAIL, 1),
            make_result(make_gate("P1-FAIL", "P1"), oc.STATUS_FAIL, 1),
            make_result(make_gate("P2-FAIL", "P2"), oc.STATUS_FAIL, 1),
            make_result(make_gate("P0-OK", "P0"), oc.STATUS_PASS, 0),
        ]

    def test_fail_on_none_blocks_nothing(self):
        assert oc.evaluate(self._results(), "none") == []

    def test_fail_on_p0_blocks_only_p0(self):
        ids = {r.gate.id for r in oc.evaluate(self._results(), "P0")}
        assert ids == {"P0-FAIL"}

    def test_fail_on_p1_blocks_p0_and_p1(self):
        ids = {r.gate.id for r in oc.evaluate(self._results(), "P1")}
        assert ids == {"P0-FAIL", "P1-FAIL"}

    def test_fail_on_p2_blocks_all_failures(self):
        ids = {r.gate.id for r in oc.evaluate(self._results(), "P2")}
        assert ids == {"P0-FAIL", "P1-FAIL", "P2-FAIL"}

    def test_error_status_is_blocking(self):
        res = [make_result(make_gate("E", "P0"), oc.STATUS_ERROR, 127)]
        assert oc.evaluate(res, "P0") == res


# ─── report ────────────────────────────────────────────────────────────────


class TestReport:
    def test_format_line_ok(self):
        res = make_result(make_gate("OS-CHK-DOC-01", "P0"), oc.STATUS_PASS)
        res.detail = "64/64 files pass"
        line = oc.format_line(res, "2026-07-07 18:30:00")
        assert line == ("[2026-07-07 18:30:00] [  OK  ] "
                        "OS-CHK-DOC-01 t: 64/64 files pass")

    def test_format_line_error(self):
        res = make_result(make_gate("OS-CHK-DOC-02", "P0"), oc.STATUS_FAIL, 1)
        res.detail = "2 violations"
        line = oc.format_line(res, "T")
        assert "[ERROR]" in line and line.endswith(": 2 violations")

    def test_check_mode_pass_line(self, capsys):
        results = [make_result(make_gate("A", "P0"), oc.STATUS_PASS)]
        oc.print_report(results, "1.0.0", 1.23, True, False, "T")
        out = capsys.readouterr().out
        assert out.strip() == "OS-CHK: PASS (1/1 gates, 1.2s)"

    def test_check_mode_fail_lists_gate(self, capsys):
        results = [make_result(make_gate("A", "P0"), oc.STATUS_FAIL, 1)]
        results[0].detail = "bad"
        oc.print_report(results, "1.0.0", 0.5, True, False, "T")
        out = capsys.readouterr().out
        assert "OS-CHK: FAIL" in out
        assert "A t: bad" in out

    def test_verbose_renders_output(self, capsys):
        res = make_result(make_gate("A", "P0"), oc.STATUS_FAIL, 1)
        res.stdout = "line-1\nline-2"
        oc.print_report([res], "1.0.0", 0.5, False, True, "T")
        out = capsys.readouterr().out
        assert "| line-1" in out and "| line-2" in out
        assert "RESULT: FAIL" in out

    def test_write_json_report(self, tmp_path):
        res = make_result(make_gate("A", "P0"), oc.STATUS_PASS)
        out = tmp_path / "r.json"
        oc.write_json_report(out, "1.0.0", "T", "P1", [res])
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["registry_version"] == "1.0.0"
        assert payload["summary"] == {"total": 1, "passed": 1, "failed": 0}
        assert payload["results"][0]["id"] == "A"
        assert payload["fail_on"] == "P1"


# ─── CLI ───────────────────────────────────────────────────────────────────


class TestCli:
    def test_list(self, repo_root, capsys):
        rc = oc.main(["--repo-root", str(repo_root), "--list"])
        out = capsys.readouterr().out
        assert rc == oc.EXIT_PASS
        assert "OS-IRON-001" in out
        assert "P0" in out

    def test_check_end_to_end_real_repo(self, repo_root):
        assert oc.main(["--repo-root", str(repo_root), "--check"]) == \
            oc.EXIT_PASS

    def test_unknown_gate_id(self, repo_root, capsys):
        rc = oc.main(["--repo-root", str(repo_root), "--gate", "NOPE"])
        assert rc == oc.EXIT_USAGE
        assert "未知门禁" in capsys.readouterr().err

    def test_missing_registry(self, repo_root, tmp_path, capsys):
        rc = oc.main(["--repo-root", str(repo_root),
                      "--registry", str(tmp_path / "none.yaml")])
        assert rc == oc.EXIT_USAGE
        assert "不存在" in capsys.readouterr().err

    def test_bad_timeout(self, repo_root, capsys):
        rc = oc.main(["--repo-root", str(repo_root), "--timeout", "0"])
        assert rc == oc.EXIT_USAGE
        assert "timeout" in capsys.readouterr().err

    def test_repo_root_missing(self, tmp_path, capsys):
        rc = oc.main(["--repo-root", str(tmp_path / "nope")])
        assert rc == oc.EXIT_USAGE

    def test_invalid_registry_schema(self, repo_root, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text('version: "1"\ngates:\n  - id: A\n', encoding="utf-8")
        rc = oc.main(["--repo-root", str(repo_root), "--registry", str(bad)])
        assert rc == oc.EXIT_USAGE
        assert "ERROR" in capsys.readouterr().err

    def test_json_output(self, repo_root, tmp_path):
        out = tmp_path / "report.json"
        rc = oc.main(["--repo-root", str(repo_root), "--check",
                      "--json", str(out)])
        assert rc == oc.EXIT_PASS
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["summary"]["failed"] == 0
        assert payload["summary"]["total"] >= 6

    def test_fail_on_none_never_blocks(self, repo_root, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            'version: "1"\n'
            'gates:\n'
            '  - id: A\n    name: n\n    severity: P0\n'
            '    executor: {argv: ["python3", "-c", "import sys; '
            'sys.exit(1)"]}\n',
            encoding="utf-8")
        rc = oc.main(["--repo-root", str(repo_root), "--registry", str(bad),
                      "--fail-on", "none", "--check"])
        assert rc == oc.EXIT_PASS

    def test_fail_on_p0_blocks(self, repo_root, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            'version: "1"\n'
            'gates:\n'
            '  - id: A\n    name: n\n    severity: P0\n'
            '    executor: {argv: ["python3", "-c", "import sys; '
            'sys.exit(1)"]}\n',
            encoding="utf-8")
        rc = oc.main(["--repo-root", str(repo_root), "--registry", str(bad),
                      "--fail-on", "P0", "--check"])
        assert rc == oc.EXIT_BLOCKED

    def test_gate_filter_runs_subset(self, repo_root, capsys):
        rc = oc.main(["--repo-root", str(repo_root), "--check",
                      "--gate", "OS-IRON-001"])
        out = capsys.readouterr().out
        assert rc == oc.EXIT_PASS
        assert "(1/1 gates" in out


# ─── Shipped registry integrity ────────────────────────────────────────────


class TestShippedRegistry:
    def test_registry_parses(self, repo_root):
        text = (repo_root / oc.DEFAULT_REGISTRY_REL).read_text(
            encoding="utf-8")
        version, gates = oc.parse_registry(text)
        assert version
        assert len(gates) >= 6

    def test_all_executors_exist(self, repo_root):
        text = (repo_root / oc.DEFAULT_REGISTRY_REL).read_text(
            encoding="utf-8")
        _, gates = oc.parse_registry(text)
        for gate in gates:
            driver = gate.argv[0]
            suffix = EXECUTOR_SUFFIX.get(driver)
            assert suffix, \
                f"{gate.id}: 执行器驱动 {driver!r} 未登记（需先扩展 EXECUTOR_SUFFIX）"
            script = next(
                (a for a in gate.argv if a.endswith(suffix)), None)
            assert script, f"{gate.id}: 未找到 {suffix} 执行器"
            assert (repo_root / script).is_file(), \
                f"{gate.id}: 执行器缺失 {script}"

    def test_severities_valid(self, repo_root):
        text = (repo_root / oc.DEFAULT_REGISTRY_REL).read_text(
            encoding="utf-8")
        _, gates = oc.parse_registry(text)
        assert all(g.severity in oc.SEVERITY_ORDER for g in gates)
