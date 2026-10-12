#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""cap_gen 单元测试 — capability ID 契约解析、跨契约校验与渲染。

覆盖 tools/codegen/cap_gen.py：X-macro 行解析、AIRY_ 前缀转换、重复符号
检测、§5.4 syscall.xml 引用校验、上下文构造与零漂移校验。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cap_gen
from cap_gen import (
    CAP_DEF_REL,
    OUTPUTS,
    _capability_required,
    _is_unconstrained,
    _known_symbols,
    _load_xml_root,
    _make_cap_spec,
    _parse_cap_line,
    _prefix_symbol,
    _register_symbol,
    build_context,
    check,
    parse_cap_ids_def,
    render_outputs,
    validate_capability_references,
)
from codegen_types import (
    CAP_EXTENSION_BASE,
    CAP_ID_COUNT,
    ContractSourceError,
    ContractValidationError,
    ExitCode,
)


def _write_def(tmp_path: Path, lines: list[str]) -> Path:
    target = tmp_path / "airy_cap_ids.def"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _full_def_lines() -> list[str]:
    return [f'AIRY_CAP_ID(CAP_T{i}, {i}, "test cap {i}")' for i in range(CAP_ID_COUNT)]


def _write_xml(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "syscall.xml"
    target.write_text(f'<syscalls version="0x0100" core_count="1">{body}</syscalls>', encoding="utf-8")
    return target


class TestPrefixSymbol:
    def test_adds_airy_prefix(self) -> None:
        assert _prefix_symbol("CAP_CHOWN") == "AIRY_CAP_CHOWN"

    def test_keeps_existing_prefix(self) -> None:
        assert _prefix_symbol("AIRY_CAP_X") == "AIRY_CAP_X"


class TestParseCapLine:
    def test_valid_line(self) -> None:
        assert _parse_cap_line('  AIRY_CAP_ID(CAP_CHOWN, 0, "chown")  ') == ("CAP_CHOWN", 0, "chown")

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "// comment",
            'AIRY_CAP_ID(CAP_X, notanint, "d")',
            'AIRY_CAP_ID(CAP_X, 1)',
            'AIRY_CAP_ID( CAP_X , 1 , d )',
        ],
    )
    def test_invalid_lines_return_none(self, raw: str) -> None:
        assert _parse_cap_line(raw) is None


class TestRegisterSymbol:
    def test_new_symbol_registered(self) -> None:
        seen: dict[str, int] = {}
        _register_symbol("AIRY_CAP_X", 3, seen, Path("f.def"))
        assert seen == {"AIRY_CAP_X": 3}

    def test_duplicate_rejected(self) -> None:
        seen = {"AIRY_CAP_X": 3}
        with pytest.raises(ContractValidationError, match="duplicate capability symbol AIRY_CAP_X"):
            _register_symbol("AIRY_CAP_X", 9, seen, Path("f.def"))


class TestMakeCapSpec:
    def test_prefix_and_describe(self) -> None:
        spec = _make_cap_spec(("CAP_FOO", 5, "foo cap"), Path("f.def"), 1, {})
        assert spec.symbol == "AIRY_CAP_FOO"
        assert spec.raw_symbol == "CAP_FOO"
        assert spec.value == 5

    def test_out_of_range_value_prefixed(self) -> None:
        with pytest.raises(ContractValidationError, match=r"f\.def:7: capability id out of range: 99"):
            _make_cap_spec(("CAP_FOO", 99, "d"), Path("f.def"), 7, {})


class TestParseCapIdsDef:
    def test_real_contract(self, repo_root: Path) -> None:
        contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
        assert len(contract.cap_ids) == CAP_ID_COUNT
        assert contract.cap_ids[0].symbol == "AIRY_CAP_CHOWN"
        assert contract.cap_ids[0].value == 0
        assert contract.cap_ids[-1].value == CAP_ID_COUNT - 1
        assert len(contract.posix_caps) == CAP_EXTENSION_BASE
        assert len(contract.extension_caps) == CAP_ID_COUNT - CAP_EXTENSION_BASE

    def test_synthetic_roundtrip(self, tmp_path: Path) -> None:
        contract = parse_cap_ids_def(_write_def(tmp_path, _full_def_lines()))
        assert sorted(c.value for c in contract.cap_ids) == list(range(CAP_ID_COUNT))

    def test_empty_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError, match="no AIRY_CAP_ID entries parsed"):
            parse_cap_ids_def(_write_def(tmp_path, ["// nothing here"]))

    def test_missing_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError, match="not found"):
            parse_cap_ids_def(tmp_path / "absent.def")

    def test_duplicate_symbol_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractValidationError, match="duplicate capability symbol"):
            parse_cap_ids_def(
                _write_def(tmp_path, ['AIRY_CAP_ID(CAP_A, 0, "x")', 'AIRY_CAP_ID(CAP_A, 1, "y")'])
            )


class TestXmlHelpers:
    def test_load_missing_xml(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError, match="not found"):
            _load_xml_root(tmp_path / "absent.xml")

    def test_load_malformed_xml(self, tmp_path: Path) -> None:
        bad = tmp_path / "syscall.xml"
        bad.write_text("<syscalls>", encoding="utf-8")
        with pytest.raises(ContractSourceError, match="XML parse error"):
            _load_xml_root(bad)

    def test_known_symbols_include_both_forms(self, repo_root: Path) -> None:
        contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
        known = _known_symbols(contract)
        assert "AIRY_CAP_CHOWN" in known
        assert "CAP_CHOWN" in known

    def test_capability_required_defaults_empty(self, tmp_path: Path) -> None:
        root = _load_xml_root(_write_xml(tmp_path, "<syscall><name>airy_sys_call</name></syscall>"))
        assert _capability_required(root.find("syscall")) == ""

    def test_is_unconstrained(self) -> None:
        assert _is_unconstrained("") is True
        assert _is_unconstrained("0") is True
        assert _is_unconstrained("AIRY_CAP_X") is False


class TestValidateCapabilityReferences:
    def test_undefined_capability_rejected(self, tmp_path: Path, repo_root: Path) -> None:
        contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
        xml = _write_xml(
            tmp_path,
            "<syscall><name>airy_sys_x</name>"
            "<capability_required>AIRY_CAP_NOPE</capability_required></syscall>",
        )
        with pytest.raises(ContractValidationError, match="references undefined capability"):
            validate_capability_references(contract, xml)

    def test_defined_capability_accepted(self, tmp_path: Path, repo_root: Path) -> None:
        contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
        xml = _write_xml(
            tmp_path,
            "<syscall><name>airy_sys_x</name>"
            "<capability_required>AIRY_CAP_CHOWN</capability_required></syscall>",
        )
        validate_capability_references(contract, xml)

    def test_unconstrained_accepted(self, tmp_path: Path, repo_root: Path) -> None:
        contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
        xml = _write_xml(tmp_path, "<syscall><name>airy_sys_x</name></syscall>")
        validate_capability_references(contract, xml)


class TestBuildAndRender:
    def test_build_context_shape(self, repo_root: Path) -> None:
        context = build_context(parse_cap_ids_def(repo_root / CAP_DEF_REL))
        assert set(context) == {
            "generator",
            "generator_version",
            "source_name",
            "source_path",
            "cap_id_count",
            "cap_id_max_symbol",
            "symbol_width",
            "cap_ids",
            "posix_caps",
            "extension_caps",
        }
        assert context["cap_id_count"] == CAP_ID_COUNT

    def test_render_outputs_paths_and_content(self, repo_root: Path) -> None:
        rendered = render_outputs(repo_root)
        assert set(rendered) == {repo_root / rel for _name, rel in OUTPUTS}
        assert all(content.endswith("\n") for content in rendered.values())

    def test_render_is_deterministic(self, repo_root: Path) -> None:
        assert render_outputs(repo_root) == render_outputs(repo_root)

    def test_check_matches_committed(self, repo_root: Path) -> None:
        assert check(repo_root) == int(ExitCode.SUCCESS)
