# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/nightly 单元测试：清单 schema / 触发条件 / 单实例并发 / 最小权限 /
job 拓扑 / runner / 超时预算 / shell 变量 / helper 登记与契约 / CI 预算 /
CLI 与真实仓库端到端。

测试以真实清单与真实 workflow 为基线做受控变异，逐项断言各检查器的
通过/拒绝语义；另在真实仓库上运行 --check，作为端到端回归。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

import nightly as nt

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = nt.load_yaml(TOOL_DIR / "manifest.yaml")
WORKFLOW = nt.load_yaml(REPO_ROOT / MANIFEST["workflow"])

JOB_TEST = "nightly-test-suite"
JOB_REVERT = "nightly-revert-or-budget"


def _man() -> dict:
    return copy.deepcopy(MANIFEST)


def _wf() -> dict:
    return copy.deepcopy(WORKFLOW)


def _triggers(wf: dict) -> dict:
    return nt._triggers(wf)


def _steps(wf: dict, job: str) -> list[dict]:
    return wf["jobs"][job]["steps"]


def _dump(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8")


# ─── Schema ────────────────────────────────────────────────────────────────


def test_schema_accepts_real_manifest():
    assert nt.check_schema(_man()).passed


def test_schema_rejects_missing_section():
    man = _man()
    del man["budget"]
    result = nt.check_schema(man)
    assert not result.passed
    assert "budget" in result.details


def test_schema_rejects_bad_cron_type():
    man = _man()
    man["triggers"]["cron"] = 2
    assert not nt.check_schema(man).passed


def test_schema_rejects_unknown_helper_state():
    man = _man()
    man["helpers"]["soak-runner"]["status"] = "todo"
    assert not nt.check_schema(man).passed


def test_schema_rejects_budget_argv_drift():
    man = _man()
    man["budget"]["argv"] = ["--max-minutes"]
    result = nt.check_schema(man)
    assert not result.passed
    assert "max_minutes" in result.details


def test_schema_rejects_empty_jobs():
    man = _man()
    man["jobs"] = []
    assert not nt.check_schema(man).passed


def test_schema_rejects_duplicate_job_names():
    man = _man()
    man["jobs"].append(copy.deepcopy(man["jobs"][0]))
    assert not nt.check_schema(man).passed


def test_schema_short_circuits_other_checks():
    man = _man()
    del man["triggers"]
    results = nt.build_checks(man, _wf(), REPO_ROOT)
    assert len(results) == 1
    assert not results[0].passed


# ─── Triggers ──────────────────────────────────────────────────────────────


def test_trigger_accepts_real_workflow():
    assert nt.check_trigger(_man(), _wf()).passed


def test_trigger_rejects_cron_drift():
    wf = _wf()
    _triggers(wf)["schedule"][0]["cron"] = "0 3 * * *"
    result = nt.check_trigger(_man(), wf)
    assert not result.passed
    assert "0 2 * * *" in result.details


def test_trigger_rejects_pull_request():
    wf = _wf()
    _triggers(wf)["pull_request"] = {}
    result = nt.check_trigger(_man(), wf)
    assert not result.passed
    assert "pull_request" in result.details


def test_trigger_rejects_missing_dispatch():
    wf = _wf()
    del _triggers(wf)["workflow_dispatch"]
    assert not nt.check_trigger(_man(), wf).passed


# ─── Concurrency ───────────────────────────────────────────────────────────


def test_flight_accepts_real_workflow():
    assert nt.check_flight(_man(), _wf()).passed


def test_flight_rejects_missing_concurrency():
    wf = _wf()
    del wf["concurrency"]
    assert not nt.check_flight(_man(), wf).passed


def test_flight_rejects_cancel_in_progress():
    wf = _wf()
    wf["concurrency"]["cancel-in-progress"] = True
    assert not nt.check_flight(_man(), wf).passed


def test_flight_rejects_group_drift():
    wf = _wf()
    wf["concurrency"]["group"] = "nightly-${{ github.ref }}"
    assert not nt.check_flight(_man(), wf).passed


# ─── Permissions ───────────────────────────────────────────────────────────


def test_perms_accepts_real_workflow():
    assert nt.check_perms(_man(), _wf()).passed


def test_perms_rejects_missing_block():
    wf = _wf()
    del wf["permissions"]
    assert not nt.check_perms(_man(), wf).passed


def test_perms_rejects_write_on_contents():
    wf = _wf()
    wf["permissions"]["contents"] = "write"
    assert not nt.check_perms(_man(), wf).passed


def test_perms_rejects_extra_write_scope():
    wf = _wf()
    wf["permissions"]["issues"] = "write"
    result = nt.check_perms(_man(), wf)
    assert not result.passed
    assert "issues" in result.details


# ─── Jobs ──────────────────────────────────────────────────────────────────


def test_jobs_accepts_real_workflow():
    assert nt.check_jobs(_man(), _wf()).passed


def test_jobs_rejects_missing_job():
    wf = _wf()
    del wf["jobs"][JOB_REVERT]
    result = nt.check_jobs(_man(), wf)
    assert not result.passed
    assert JOB_REVERT in result.details


def test_jobs_rejects_unregistered_job():
    wf = _wf()
    wf["jobs"]["extra"] = copy.deepcopy(wf["jobs"][JOB_TEST])
    result = nt.check_jobs(_man(), wf)
    assert not result.passed
    assert "extra" in result.details


def test_jobs_rejects_needs_drift():
    wf = _wf()
    wf["jobs"][JOB_REVERT]["needs"] = []
    assert not nt.check_jobs(_man(), wf).passed


def test_jobs_rejects_missing_always():
    wf = _wf()
    del wf["jobs"][JOB_REVERT]["if"]
    assert not nt.check_jobs(_man(), wf).passed


# ─── Runner and timeout ────────────────────────────────────────────────────


def test_runner_accepts_real_workflow():
    assert nt.check_runner(_man(), _wf()).passed


def test_runner_rejects_other_label():
    wf = _wf()
    wf["jobs"][JOB_TEST]["runs-on"] = "ubuntu-22.04"
    assert not nt.check_runner(_man(), wf).passed


def test_timeout_accepts_real_workflow():
    assert nt.check_timeout(_man(), _wf()).passed


def test_timeout_rejects_hosted_ceiling():
    wf = _wf()
    wf["jobs"][JOB_TEST]["timeout-minutes"] = 4320
    result = nt.check_timeout(_man(), wf)
    assert not result.passed
    assert "上限" in result.details


def test_timeout_rejects_missing_value():
    wf = _wf()
    del wf["jobs"][JOB_REVERT]["timeout-minutes"]
    assert not nt.check_timeout(_man(), wf).passed


# ─── Shell variables ───────────────────────────────────────────────────────


def test_vars_accepts_real_workflow():
    assert nt.check_vars(_man(), _wf()).passed


def test_vars_rejects_undefined_commit():
    wf = _wf()
    del wf["jobs"][JOB_REVERT]["env"]
    result = nt.check_vars(_man(), wf)
    assert not result.passed
    assert "$COMMIT" in result.details


def test_vars_accepts_inline_assignment():
    wf = _wf()
    del wf["jobs"][JOB_REVERT]["env"]
    steps = _steps(wf, JOB_REVERT)
    steps[1]["run"] = "COMMIT=$(git rev-parse HEAD)\n" + steps[1]["run"]
    assert nt.check_vars(_man(), wf).passed


def test_vars_ignores_github_expressions():
    wf = _wf()
    step = next(s for s in _steps(wf, JOB_TEST) if "run" in s)
    step["run"] = "echo ${{ github.sha }}\n" + step["run"]
    assert nt.check_vars(_man(), wf).passed


# ─── Helper surface ────────────────────────────────────────────────────────


def test_surface_accepts_real_readme():
    assert nt.check_surface(_man(), _wf(), REPO_ROOT).passed


def test_surface_rejects_missing_readme(tmp_path):
    assert not nt.check_surface(_man(), _wf(), tmp_path).passed


def test_surface_rejects_unregistered_helper(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/README.md").write_text("none\n", encoding="utf-8")
    result = nt.check_surface(_man(), _wf(), tmp_path)
    assert not result.passed
    assert "formal-verify.py" in result.details


# ─── Helper contract ───────────────────────────────────────────────────────


def _mini(status: str = "deferred") -> dict:
    return {
        "jobs": [{"name": "j", "timeout_minutes": 1, "needs": [],
                  "helpers": ["h"]}],
        "helpers": {"h": {"path": "tools/h.py", "status": status,
                          "argv": ["--target"]}},
    }


def _wf_run(script: str) -> dict:
    return {"jobs": {"j": {"steps": [{"run": script}]}}}


def _guarded(path: str = "tools/h.py") -> str:
    return (f"if [ -f {path} ]; then\n"
            f"  python3 {path} --target x\n"
            "else\n"
            '  echo "SKIP: not implemented"\n'
            "fi\n")


def test_helpers_accept_deferred_guard(tmp_path):
    wf = _wf_run(_guarded())
    assert nt.check_helpers(_mini(), wf, tmp_path).passed


def test_helpers_reject_missing_guard(tmp_path):
    wf = _wf_run("python3 tools/h.py --target x\n")
    result = nt.check_helpers(_mini(), wf, tmp_path)
    assert not result.passed
    assert "存在性守卫" in result.details


def test_helpers_reject_missing_skip_marker(tmp_path):
    script = _guarded().replace("SKIP: ", "")
    result = nt.check_helpers(_mini(), _wf_run(script), tmp_path)
    assert not result.passed
    assert "SKIP" in result.details


def test_helpers_reject_missing_argv(tmp_path):
    script = _guarded().replace(" --target x", "")
    result = nt.check_helpers(_mini(), _wf_run(script), tmp_path)
    assert not result.passed
    assert "--target" in result.details


def test_helpers_reject_deferred_but_present(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/h.py").write_text("", encoding="utf-8")
    result = nt.check_helpers(_mini(), _wf_run(_guarded()), tmp_path)
    assert not result.passed
    assert "已落地" in result.details


def test_helpers_accept_available(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/h.py").write_text("", encoding="utf-8")
    wf = _wf_run("python3 tools/h.py --target x\n")
    assert nt.check_helpers(_mini("available"), wf, tmp_path).passed


def test_helpers_reject_available_without_file(tmp_path):
    wf = _wf_run("python3 tools/h.py --target x\n")
    result = nt.check_helpers(_mini("available"), wf, tmp_path)
    assert not result.passed
    assert "不存在" in result.details


def test_helpers_reject_available_with_guard(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/h.py").write_text("", encoding="utf-8")
    result = nt.check_helpers(_mini("available"), _wf_run(_guarded()),
                              tmp_path)
    assert not result.passed
    assert "存在性守卫" in result.details


def test_helpers_reject_unknown_reference(tmp_path):
    man = _mini()
    man["jobs"][0]["helpers"] = ["absent"]
    result = nt.check_helpers(man, _wf_run(_guarded()), tmp_path)
    assert not result.passed
    assert "absent" in result.details


def test_helpers_reject_missing_call(tmp_path):
    result = nt.check_helpers(_mini(), _wf_run("echo hello\n"), tmp_path)
    assert not result.passed
    assert "未调用" in result.details


# ─── CI budget ─────────────────────────────────────────────────────────────


def test_budget_accepts_real_workflow():
    assert nt.check_budget(_man(), _wf()).passed


def test_budget_rejects_value_drift():
    wf = _wf()
    step = _steps(wf, JOB_REVERT)[-1]
    step["run"] = step["run"].replace("--max-minutes 60",
                                      "--max-minutes 90")
    result = nt.check_budget(_man(), wf)
    assert not result.passed
    assert "--max-minutes 60" in result.details


def test_budget_rejects_missing_call():
    wf = _wf()
    wf["jobs"][JOB_REVERT]["steps"] = []
    assert not nt.check_budget(_man(), wf).passed


# ─── CLI ───────────────────────────────────────────────────────────────────


def test_cli_real_repo_passes(capsys):
    code = nt.main(["--repo-root", str(REPO_ROOT), "--check"])
    captured = capsys.readouterr()
    assert code == nt.EXIT_PASS
    assert captured.out.startswith("NIGHTLY: PASS")


def test_cli_rejects_missing_root(tmp_path):
    absent = tmp_path / "absent"
    assert nt.main(["--repo-root", str(absent), "--check"]) == nt.EXIT_USAGE


def test_cli_rejects_missing_manifest(tmp_path):
    assert nt.main(["--repo-root", str(tmp_path),
                    "--check"]) == nt.EXIT_USAGE


def test_cli_rejects_invalid_manifest(tmp_path):
    target = tmp_path / "tools/nightly"
    target.mkdir(parents=True)
    (target / "manifest.yaml").write_text("a: [\n", encoding="utf-8")
    assert nt.main(["--repo-root", str(tmp_path),
                    "--check"]) == nt.EXIT_USAGE


def test_cli_rejects_schema_failure(tmp_path, capsys):
    man = _man()
    del man["budget"]
    target = tmp_path / "tools/nightly"
    target.mkdir(parents=True)
    _dump(target / "manifest.yaml", man)
    code = nt.main(["--repo-root", str(tmp_path), "--check"])
    captured = capsys.readouterr()
    assert code == nt.EXIT_FAIL
    assert "NIGHTLY: FAIL" in captured.out


def test_cli_rejects_missing_workflow(tmp_path, capsys):
    target = tmp_path / "tools/nightly"
    target.mkdir(parents=True)
    _dump(target / "manifest.yaml", _man())
    code = nt.main(["--repo-root", str(tmp_path), "--check"])
    captured = capsys.readouterr()
    assert code == nt.EXIT_FAIL
    assert "workflow 可解析" in captured.out


def test_cli_honours_manifest_override(tmp_path):
    custom = tmp_path / "custom.yaml"
    _dump(custom, _man())
    assert nt.main(["--repo-root", str(REPO_ROOT),
                    "--manifest", str(custom),
                    "--check"]) == nt.EXIT_PASS
