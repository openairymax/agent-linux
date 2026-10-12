# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_semantics — AirymaxOS 策略 DSL 语义校验与 IR 构造。

在 dsl_parser 产出的 Program 语法树上执行跨节点语义校验，并降为可直接
codegen 的 ProgramIR。校验规则：

  1. version 必须等于 DSL_VERSION；
  2. 表名全局唯一；钩子必须属于 Airy LSM 7 个已实现钩子；
  3. 每张表至少 1 个 input，input 名表内唯一；
  4. 每张表至少 1 条 rule，rule 编号表内唯一且 > 0；
  5. when 引用的字段必须已声明；字面量不得超过字段类型的取值范围；
  6. fault 仅允许出现在 DENY 规则上，且必须属于 Fault 码表；
  7. 行按 rule 编号升序排列（评估采用 first-match，编号小者优先）。

任一校验失败抛 DslSemanticError（携带列位置诊断）。本模块不做 codegen，
仅产出语义合法的 ProgramIR。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

from dsl_types import (
    DSL_VERSION,
    FAULT_CODES,
    LSM_HOOKS,
    Condition,
    ConditionIR,
    DslSemanticError,
    InputDecl,
    InputIR,
    Program,
    ProgramIR,
    RowIR,
    Rule,
    SourceSpan,
    Table,
    TableIR,
    Verdict,
    format_diagnostic,
)

# 2. 公开导出
__all__ = ["to_ir"]


def _fail(source_name: str, span: SourceSpan, message: str) -> None:
    raise DslSemanticError(format_diagnostic(source_name, span, message))


def _check_version(version: str, source_name: str) -> None:
    if version != DSL_VERSION:
        raise DslSemanticError(
            f"{source_name}: unsupported DSL version {version!r} (expected {DSL_VERSION!r})"
        )


def _check_unique(
    name: str, seen: dict[str, SourceSpan], source_name: str, span: SourceSpan, what: str
) -> None:
    if name in seen:
        _fail(source_name, span, f"duplicate {what} {name!r} (first declared at {seen[name]})")
    seen[name] = span


def _check_hook(table: Table, source_name: str) -> None:
    if table.hook not in LSM_HOOKS:
        expected = ", ".join(sorted(LSM_HOOKS))
        _fail(
            source_name,
            table.span,
            f"unknown LSM hook {table.hook!r} (expected one of: {expected})",
        )


def _build_inputs(
    declarations: tuple[InputDecl, ...], table: Table, source_name: str
) -> tuple[tuple[InputIR, ...], dict[str, InputIR]]:
    if not declarations:
        _fail(source_name, table.span, f"table {table.name!r} requires at least one input field")
    seen: dict[str, SourceSpan] = {}
    inputs: list[InputIR] = []
    for index, decl in enumerate(declarations):
        _check_unique(decl.name, seen, source_name, decl.span, "input field")
        inputs.append(InputIR(name=decl.name, value_type=decl.value_type, index=index))
    return tuple(inputs), {item.name: item for item in inputs}


def _build_condition(
    condition: Condition, index: dict[str, InputIR], source_name: str
) -> ConditionIR:
    field = index.get(condition.field)
    if field is None:
        _fail(
            source_name,
            condition.span,
            f"condition references undeclared field {condition.field!r}",
        )
    if condition.value > field.value_type.max_value:
        _fail(
            source_name,
            condition.span,
            f"literal {condition.value} out of range for {condition.field!r} "
            f"({field.value_type.value}, max {field.value_type.max_value})",
        )
    return ConditionIR(
        field_index=field.index,
        field_name=field.name,
        op=condition.op,
        value=condition.value,
    )


def _check_fault(rule: Rule, source_name: str) -> None:
    if rule.fault is None:
        return
    if rule.verdict is not Verdict.DENY:
        _fail(
            source_name,
            rule.span,
            f"fault code only allowed on DENY rules, got {rule.verdict.name}",
        )
    if rule.fault not in FAULT_CODES:
        _fail(source_name, rule.span, f"unknown fault code {rule.fault:#06x}")


def _build_rule(rule: Rule, index: dict[str, InputIR], source_name: str) -> RowIR:
    _check_fault(rule, source_name)
    conditions = tuple(_build_condition(cond, index, source_name) for cond in rule.conditions)
    return RowIR(
        number=rule.number,
        conditions=conditions,
        verdict=rule.verdict,
        fault=rule.fault,
    )


def _build_rows(
    rules: tuple[Rule, ...], index: dict[str, InputIR], table: Table, source_name: str
) -> tuple[RowIR, ...]:
    if not rules:
        _fail(source_name, table.span, f"table {table.name!r} requires at least one rule")
    seen: dict[str, SourceSpan] = {}
    rows: list[RowIR] = []
    for rule in sorted(rules, key=lambda item: item.number):
        if rule.number <= 0:
            _fail(source_name, rule.span, f"rule number must be positive, got {rule.number}")
        _check_unique(f"rule {rule.number}", seen, source_name, rule.span, "rule number")
        rows.append(_build_rule(rule, index, source_name))
    return tuple(rows)


def _build_table(table: Table, source_name: str) -> TableIR:
    _check_hook(table, source_name)
    inputs, index = _build_inputs(table.inputs, table, source_name)
    rows = _build_rows(table.rules, index, table, source_name)
    return TableIR(
        name=table.name,
        hook=table.hook,
        inputs=inputs,
        rows=rows,
        default_verdict=table.default_verdict,
    )


def to_ir(program: Program, source_name: str = "<adl>") -> ProgramIR:
    """校验 Program 语法树并降为语义合法的 ProgramIR。"""
    _check_version(program.version, source_name)
    seen: dict[str, SourceSpan] = {}
    tables: list[TableIR] = []
    for table in program.tables:
        _check_unique(table.name, seen, source_name, table.span, "table")
        tables.append(_build_table(table, source_name))
    return ProgramIR(version=program.version, source_name=source_name, tables=tuple(tables))
