#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""codegen_io 单元测试 — 渲染环境、产物写入、零漂移校验与 CLI 入口。

覆盖 tools/codegen/codegen_io.py 的全部公共契约，验证各生成器共享的
确定性渲染与写盘/校验脚手架行为（06-codegen-pipeline.md §8/§9.3）。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

import codegen_io
from codegen_io import (
    build_parser,
    check,
    default_repo_root,
    fail,
    generate,
    has_drift,
    main,
    make_env,
    read_text,
    render,
    require_file,
    write_outputs,
)
from codegen_types import ContractSourceError, ExitCode, TemplateRenderError


def _write_template(tmp_path: Path, name: str, body: str) -> Path:
    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / name).write_text(body, encoding="utf-8")
    return template_dir


class TestMakeEnv:
    def test_deterministic_flags(self) -> None:
        env = make_env()
        assert env.trim_blocks is True
        assert env.lstrip_blocks is True
        assert env.keep_trailing_newline is True
        assert env.autoescape is False


class TestRequireFileAndRead:
    def test_require_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.txt"
        with pytest.raises(ContractSourceError, match="contract source not found"):
            require_file(missing, "contract source")

    def test_require_file_present(self, tmp_path: Path) -> None:
        present = tmp_path / "ok.txt"
        present.write_text("x", encoding="utf-8")
        require_file(present, "contract source")

    def test_read_text_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "data.txt"
        target.write_text("hello\n", encoding="utf-8")
        assert read_text(target, "data") == "hello\n"

    def test_read_text_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError):
            read_text(tmp_path / "absent.txt", "data")


class TestRender:
    def test_render_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            codegen_io, "TEMPLATE_DIR", _write_template(tmp_path, "t.j2", "v={{ value }}")
        )
        assert render(make_env(), "t.j2", {"value": 7}) == "v=7"

    def test_render_template_error_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            codegen_io,
            "TEMPLATE_DIR",
            _write_template(tmp_path, "bad.j2", "{{ x | no_such_filter }}"),
        )
        with pytest.raises(TemplateRenderError, match="bad.j2:"):
            render(make_env(), "bad.j2", {"x": 1})


class TestWriteOutputs:
    def test_write_creates_nested_dirs(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "gen.h"
        write_outputs({out: "content\n"})
        assert out.read_text(encoding="utf-8") == "content\n"

    def test_write_if_changed_skips_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "gen.h"
        write_outputs({out: "same\n"})
        calls: list[int] = []
        original = Path.write_text

        def counting_write(self: Path, *args: object, **kwargs: object) -> int:
            calls.append(1)
            return original(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "write_text", counting_write)
        write_outputs({out: "same\n"})
        assert calls == []
        write_outputs({out: "changed\n"})
        assert calls == [1]
        assert out.read_text(encoding="utf-8") == "changed\n"


class TestFail:
    def test_fail_reports_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert fail("boom") == int(ExitCode.FAILURE)
        assert "ERROR: boom" in capsys.readouterr().err


class TestCheckOneAndDrift:
    def test_has_drift_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert has_drift({tmp_path / "gone.h": "x"}) is True
        assert "generated file missing" in capsys.readouterr().err

    def test_has_drift_none_when_identical(self, tmp_path: Path) -> None:
        out = tmp_path / "gen.h"
        out.write_text("same\n", encoding="utf-8")
        assert has_drift({out: "same\n"}) is False

    def test_has_drift_reports_diff(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "gen.h"
        out.write_text("old\n", encoding="utf-8")
        assert has_drift({out: "new\n"}) is True
        captured = capsys.readouterr().err
        assert "generated content differs from committed file" in captured
        assert "-old" in captured
        assert "+new" in captured


class TestGenerate:
    def test_generate_writes_and_reports(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "gen.h"

        def render_outputs(_root: Path) -> dict[Path, str]:
            return {out: "body\n"}

        assert generate(render_outputs, tmp_path) == int(ExitCode.SUCCESS)
        assert out.read_text(encoding="utf-8") == "body\n"
        assert f"Generated: {out}" in capsys.readouterr().out

    def test_generate_wraps_codegen_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        def render_outputs(_root: Path) -> dict[Path, str]:
            raise ContractSourceError("missing contract")

        assert generate(render_outputs, tmp_path) == int(ExitCode.FAILURE)
        assert "ERROR: missing contract" in capsys.readouterr().err


class TestCheck:
    def test_check_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "gen.h"
        out.write_text("body\n", encoding="utf-8")

        def render_outputs(_root: Path) -> dict[Path, str]:
            return {out: "body\n"}

        code = check(render_outputs, tmp_path, generator="demo_gen", make_target="demo")
        assert code == int(ExitCode.SUCCESS)
        assert "OK: demo_gen outputs match committed files (1 files)" in capsys.readouterr().out

    def test_check_drift_hint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        def render_outputs(_root: Path) -> dict[Path, str]:
            return {tmp_path / "gen.h": "body\n"}

        code = check(render_outputs, tmp_path, generator="demo_gen", make_target="demo")
        assert code == int(ExitCode.FAILURE)
        assert "Run 'make demo' to regenerate" in capsys.readouterr().err

    def test_check_wraps_codegen_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        def render_outputs(_root: Path) -> dict[Path, str]:
            raise ContractSourceError("bad source")

        code = check(render_outputs, tmp_path, generator="demo_gen", make_target="demo")
        assert code == int(ExitCode.FAILURE)
        assert "ERROR: bad source" in capsys.readouterr().err


class TestParserAndMain:
    def test_default_repo_root_is_agent_linux(self) -> None:
        assert default_repo_root() == Path(codegen_io.__file__).resolve().parents[2]

    def test_parser_defaults(self) -> None:
        parser = build_parser("demo")
        args = parser.parse_args([])
        assert args.repo_root == str(default_repo_root())
        assert args.check is False

    def test_main_generate(self, tmp_path: Path) -> None:
        out = tmp_path / "gen.h"

        def render_outputs(_root: Path) -> dict[Path, str]:
            return {out: "body\n"}

        code = main(
            render_outputs,
            description="demo",
            generator="demo_gen",
            make_target="demo",
            argv=["--repo-root", str(tmp_path)],
        )
        assert code == int(ExitCode.SUCCESS)
        assert out.read_text(encoding="utf-8") == "body\n"

    def test_main_check(self, tmp_path: Path) -> None:
        out = tmp_path / "gen.h"
        out.write_text("body\n", encoding="utf-8")

        def render_outputs(_root: Path) -> dict[Path, str]:
            return {out: "body\n"}

        code = main(
            render_outputs,
            description="demo",
            generator="demo_gen",
            make_target="demo",
            argv=["--repo-root", str(tmp_path), "--check"],
        )
        assert code == int(ExitCode.SUCCESS)

    def test_main_repo_root_resolved(self, tmp_path: Path) -> None:
        def render_outputs(root: Path) -> dict[Path, str]:
            assert root == tmp_path.resolve()
            assert root.is_absolute()
            return {}

        code = main(
            render_outputs,
            description="demo",
            generator="demo_gen",
            make_target="demo",
            argv=["--repo-root", str(tmp_path)],
        )
        assert code == int(ExitCode.SUCCESS)

    def test_write_outputs_accepts_pathlib_types(self, tmp_path: Path) -> None:
        out = tmp_path / "gen.h"
        write_outputs({out: "x\n"})
        assert isinstance(pathlib.Path(out), Path)
