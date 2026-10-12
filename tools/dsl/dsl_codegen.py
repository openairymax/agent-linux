# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_codegen — 将策略 DSL 语义 IR 渲染为纯 C 决策表头文件。

把 dsl_semantics 产出的 ProgramIR 展开为 Jinja2 上下文，复用 tools/codegen
的确定性渲染基础设施（codegen_io.make_env/render）生成自包含 C 头。生成物
属机制层：结构体、算子枚举与 static inline 求值器；策略数据（每表字段名、
行谓词、裁决、故障码）按契约源如实展开，不含任何占位或桩逻辑。裁决以裸整数
（0..3）存储，与 [SC] security_types.h 的 enum airy_verdict 对齐，不重复定义。

设计依据: docs-linux/30-interfaces/06-codegen-pipeline.md（K-1 机制与策略分离）
与 docs-linux/110-security/07-airy-lsm-design.md（7 钩子 + 4 值裁决）。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# tools/dsl 与 tools/codegen 为平级工具目录。本模块直接执行时脚本目录已在
# sys.path，但 codegen 基础设施与同级的 dsl_types 需显式注入，保证既可被
# 测试 import，也可被 `python tools/dsl/dsl_compile.py` 直接执行。
_DSL_DIR = Path(__file__).resolve().parent
_CODEGEN_DIR = _DSL_DIR.parent / "codegen"
for _path in (_DSL_DIR, _CODEGEN_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import codegen_io  # noqa: E402  (bootstrap 后导入)
from codegen_types import TemplateRenderError  # noqa: E402

from dsl_types import (  # noqa: E402
    DSL_TEMPLATE,
    DSL_VERSION,
    CompareOp,
    DslRenderError,
    ProgramIR,
    RowIR,
    TableIR,
    header_guard,
)

# 2. 公开导出
__all__ = ["TEMPLATE_DIR", "build_context", "render_program"]

# tools/dsl 自有的模板目录（与 tools/codegen/templates 分离）。
TEMPLATE_DIR: Path = _DSL_DIR / "templates"

# 生成器标识（写入生成头注释）。
_GENERATOR: str = "dsl_compile"


def _c_literal(value: int) -> str:
    """渲染无符号字面量：大值走十六进制，规避 C99 十进制常量溢出告警。"""
    if value <= 9:
        return str(value)
    return f"0x{value:X}"


def _condition_context(row: RowIR) -> list[dict[str, Any]]:
    return [
        {
            "field_index": cond.field_index,
            "field_name": cond.field_name,
            "op_macro": cond.op.c_macro,
            "value_literal": _c_literal(cond.value),
        }
        for cond in row.conditions
    ]


def _row_context(row: RowIR) -> dict[str, Any]:
    return {
        "number": row.number,
        "cond_count": len(row.conditions),
        "conds": _condition_context(row),
        "verdict_value": int(row.verdict),
        "fault_literal": "0" if row.fault is None else f"0x{row.fault:04X}",
    }


def _table_context(table: TableIR) -> dict[str, Any]:
    return {
        "name": table.name,
        "upper_name": table.name.upper(),
        "hook": table.hook,
        "prefix": table.prefix,
        "field_count": len(table.inputs),
        "field_names": [item.name for item in table.inputs],
        "row_count": len(table.rows),
        "default_verdict_value": int(table.default_verdict),
        "rows": [_row_context(row) for row in table.rows],
    }


def build_context(program: ProgramIR) -> dict[str, Any]:
    """将 ProgramIR 展开为模板上下文（纯函数，无隐式状态）。"""
    return {
        "generator": _GENERATOR,
        "generator_version": DSL_VERSION,
        "dsl_version": program.version,
        "source_name": program.source_name,
        "source_path": program.source_name,
        "header_guard": header_guard(program.source_name),
        "compare_ops": [{"macro": op.c_macro, "code": op.code} for op in CompareOp],
        "tables": [_table_context(table) for table in program.tables],
    }


def render_program(program: ProgramIR, env=None) -> str:
    """渲染单个 .adl 程序为完整 C 头内容。

    Args:
        program: 语义校验后的程序 IR。
        env:     可选 Jinja2 环境（复用调用方的确定性设置）；缺省用
                 tools/dsl/templates 构造。

    Returns:
        生成头的完整文本。

    Raises:
        DslRenderError: 模板渲染失败。
    """
    if env is None:
        env = codegen_io.make_env(TEMPLATE_DIR)
    try:
        return codegen_io.render(env, DSL_TEMPLATE, build_context(program))
    except TemplateRenderError as exc:
        raise DslRenderError(f"{program.source_name}: {exc.message}") from exc
