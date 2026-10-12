# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""run_codegen 单元测试：管道编排、依赖序、入口调用与短路行为。

设计依据: docs-linux/30-interfaces/06-codegen-pipeline.md §6.3 / §9.3

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

import run_codegen
from codegen_types import ExitCode, GeneratorName, PIPELINE_ORDER

_MODULE_ORDER = ["cap_gen", "bitfield_gen", "ipc_gen", "syscall_gen"]


class _Recorder:
    """记录 import 与入口调用顺序的假模块工厂。"""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.events: list[tuple[str, ...]] = []
        self.fail_on = fail_on

    def import_module(self, name: str):
        self.events.append(("import", name))
        module = types.SimpleNamespace(__name__=name)

        def make(entry: str):
            def entry_fn(repo_root: Path) -> int:
                self.events.append((name, entry))
                return int(ExitCode.FAILURE) if entry == self.fail_on else int(ExitCode.SUCCESS)

            return entry_fn

        module.generate = make("generate")
        module.check = make("check")
        module.check_tbl = make("check_tbl")
        return module

    def calls(self) -> list[tuple[str, ...]]:
        return [event for event in self.events if event[0] != "import"]

    def imports(self) -> list[str]:
        return [event[1] for event in self.events if event[0] == "import"]


class TestStagesTable:
    def test_stage_keys_cover_pipeline_order(self) -> None:
        assert set(run_codegen.STAGES) == set(PIPELINE_ORDER)
        assert all(name in run_codegen.STAGES for name in PIPELINE_ORDER)

    def test_stage_descriptions_present(self) -> None:
        for description, entries in run_codegen.STAGES.values():
            assert description
            assert entries

    def test_syscall_stage_has_tbl_check(self) -> None:
        _, entries = run_codegen.STAGES[GeneratorName.SYSCALL]
        assert "check" in entries
        assert "check_tbl" in entries


class TestDefaultRepoRoot:
    def test_default_repo_root(self, repo_root: Path) -> None:
        assert run_codegen.default_repo_root() == repo_root
        assert run_codegen.default_repo_root().name == "agent-linux"


class TestInvoke:
    def test_invoke_success(self) -> None:
        module = types.SimpleNamespace(__name__="demo", check=lambda root: int(ExitCode.SUCCESS))
        assert run_codegen._invoke(module, "check", Path("/tmp")) == int(ExitCode.SUCCESS)

    def test_invoke_missing_entry(self, capsys: pytest.CaptureFixture[str]) -> None:
        module = types.SimpleNamespace(__name__="demo")
        assert run_codegen._invoke(module, "check", Path("/tmp")) == int(ExitCode.FAILURE)
        assert "missing entry 'check'" in capsys.readouterr().err


class TestRunPipeline:
    def test_generate_order(self, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(run_codegen.importlib, "import_module", recorder.import_module)
        assert run_codegen.run_pipeline(repo_root, check=False) == int(ExitCode.SUCCESS)
        assert recorder.imports() == _MODULE_ORDER
        assert recorder.calls() == [(name, "generate") for name in _MODULE_ORDER]

    def test_check_order(self, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(run_codegen.importlib, "import_module", recorder.import_module)
        assert run_codegen.run_pipeline(repo_root, check=True) == int(ExitCode.SUCCESS)
        assert recorder.calls() == [
            ("cap_gen", "check"),
            ("bitfield_gen", "check"),
            ("ipc_gen", "check"),
            ("syscall_gen", "check"),
            ("syscall_gen", "check_tbl"),
        ]

    def test_short_circuit_on_failure(
        self, repo_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder(fail_on="check")
        monkeypatch.setattr(run_codegen.importlib, "import_module", recorder.import_module)
        assert run_codegen.run_pipeline(repo_root, check=True) == int(ExitCode.FAILURE)
        assert recorder.imports() == ["cap_gen"]
        assert recorder.calls() == [("cap_gen", "check")]


class TestMain:
    def test_main_check_real_repo(self, repo_root: Path) -> None:
        assert run_codegen.main(["--repo-root", str(repo_root), "--check"]) == int(ExitCode.SUCCESS)

    def test_main_default_repo_root(self, repo_root: Path) -> None:
        default_root = run_codegen.default_repo_root().resolve()
        assert default_root == repo_root.resolve()
