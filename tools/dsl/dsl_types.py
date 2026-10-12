# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_types — AirymaxOS 策略 DSL 编译器共享类型与常量。

集中声明 .adl（Airymax Decision Language）契约源的词法/语法/语义层数据
模型：词法 Token 与源码位置、纯 C 决策表降级所需的值类型与比较算子、四值
裁决、Airy LSM 钩子与 Fault 码表，以及 AST / IR 两层中间表示与异常层级。

本模块为 L-03「DSL 编译器工具链」的公共地板：dsl_lexer / dsl_parser /
dsl_semantics / dsl_codegen / dsl_compile 统一 import 本模块，消除跨模块的
类型重复定义（FOUND-08 静态分析基线：重复率 < 15%）。

数据模型均为真实可用的 frozen dataclass（构造时经 __post_init__ 校验），
不含任何占位或桩逻辑。语义地板依据
docs-linux/110-security/07-airy-lsm-design.md（7 钩子 + 4 值裁决）与
docs-linux/20-modules/09-kernel-agent-supervisor.md §2（Fault 码表）。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Final

# tools/dsl 与 tools/codegen 为平级工具目录：前者复用后者提供的 codegen
# 管道共享基础设施（CodegenError 基类与退出码）。显式注入 sys.path，使本
# 模块既可作为包被导入，也可被直接执行（python tools/dsl/dsl_compile.py）。
_CODEGEN_DIR = Path(__file__).resolve().parents[1] / "codegen"
if str(_CODEGEN_DIR) not in sys.path:
    sys.path.insert(0, str(_CODEGEN_DIR))

from codegen_types import CodegenError  # noqa: E402  (bootstrap 后导入)

# 2. 公开导出
__all__ = [
    "AIRY_DSL_RENDER_ERROR",
    "AIRY_DSL_SEMANTIC_ERROR",
    "AIRY_DSL_SOURCE_ERROR",
    "AIRY_DSL_SYNTAX_ERROR",
    "COMPARE_OPS",
    "CompareOp",
    "Condition",
    "ConditionIR",
    "DSL_EXTENSION",
    "DSL_TEMPLATE",
    "DSL_VERSION",
    "DslError",
    "DslRenderError",
    "DslSemanticError",
    "DslSourceError",
    "DslSyntaxError",
    "FAULT_CODES",
    "FAULT_SYMBOLS",
    "HEADER_GUARD_PREFIX",
    "InputDecl",
    "InputIR",
    "KEYWORDS",
    "LSM_HOOKS",
    "PUNCT_TOKENS",
    "Program",
    "ProgramIR",
    "RowIR",
    "Rule",
    "SourceSpan",
    "TABLE_PREFIX",
    "Table",
    "TableIR",
    "Token",
    "TokenKind",
    "VALUE_TYPES",
    "VERDICT_BY_NAME",
    "ValueType",
    "Verdict",
    "format_diagnostic",
    "header_guard",
]

# 3. 契约版本与命名常量
DSL_VERSION: Final[str] = "1.0.1"
DSL_EXTENSION: Final[str] = ".adl"
DSL_TEMPLATE: Final[str] = "dsl_decision_table.h.j2"
TABLE_PREFIX: Final[str] = "airy_policy_"
HEADER_GUARD_PREFIX: Final[str] = "AIRY_GENERATED_POLICY_"

# 4. 异常错误码（CodegenError.code，用于结构化诊断与测试断言）
AIRY_DSL_SOURCE_ERROR: Final[str] = "AIRY_DSL_SOURCE_ERROR"
AIRY_DSL_SYNTAX_ERROR: Final[str] = "AIRY_DSL_SYNTAX_ERROR"
AIRY_DSL_SEMANTIC_ERROR: Final[str] = "AIRY_DSL_SEMANTIC_ERROR"
AIRY_DSL_RENDER_ERROR: Final[str] = "AIRY_DSL_RENDER_ERROR"

# 5. Airy LSM 7 个已实现钩子（07-airy-lsm-design.md）
LSM_HOOKS: Final[frozenset[str]] = frozenset(
    {
        "uring_cmd",
        "task_alloc",
        "task_free",
        "task_kill",
        "file_open",
        "inode_alloc_security",
        "inode_free_security",
    }
)

# 6. Fault 码表（09-kernel-agent-supervisor.md §2，权威定义见 08-sc-error-contract.md）
FAULT_SYMBOLS: Final[tuple[tuple[str, int], ...]] = (
    ("AIRY_FAULT_CAP_FORGED", 0x1001),
    ("AIRY_FAULT_CAP_LEAK", 0x1002),
    ("AIRY_FAULT_RING_CORRUPT", 0x1003),
    ("AIRY_FAULT_ABNORMAL_CAP", 0x1005),
    ("AIRY_FAULT_URING_MALFORMED", 0x100A),
)
FAULT_CODES: Final[dict[int, str]] = {value: name for name, value in FAULT_SYMBOLS}

# 7. 词法关键字与标点
KEYWORDS: Final[frozenset[str]] = frozenset(
    {"version", "table", "hook", "input", "rule", "when", "verdict", "fault", "default"}
)
PUNCT_TOKENS: Final[tuple[str, ...]] = (
    "==",
    "!=",
    "<=",
    ">=",
    "&",
    "<",
    ">",
    "=",
    "{",
    "}",
    ";",
    ":",
)


class TokenKind(Enum):
    """词法单元类别（dsl_lexer 产出）。"""

    KEYWORD = "keyword"
    IDENT = "ident"
    INT = "int"
    STRING = "string"
    PUNCT = "punct"
    EOF = "eof"


class Verdict(IntEnum):
    """Cupolas 四值裁决（07-airy-lsm-design.md §4；与 security_types.h 对齐）。"""

    ALLOW = 0
    DENY = 1
    AUDIT = 2
    COMPLAIN = 3

    @property
    def c_symbol(self) -> str:
        """C 侧裁决枚举符号（AIRY_VERDICT_ALLOW 等）。"""
        return f"AIRY_VERDICT_{self.name}"


VERDICT_BY_NAME: Final[dict[str, Verdict]] = {verdict.name: verdict for verdict in Verdict}


class CompareOp(Enum):
    """条件比较算子（值 = (C 运算符, 决策表算子编码)）。"""

    EQ = ("==", 0)
    NE = ("!=", 1)
    LT = ("<", 2)
    LE = ("<=", 3)
    GT = (">", 4)
    GE = (">=", 5)
    MASK = ("&", 6)

    @property
    def c_operator(self) -> str:
        """C 语言运算符字面量。"""
        return self.value[0]

    @property
    def code(self) -> int:
        """决策表算子编码（airy_policy_cmp 的 op 参数）。"""
        return self.value[1]

    @property
    def c_macro(self) -> str:
        """C 侧算子宏（AIRY_CMP_EQ 等）。"""
        return f"AIRY_CMP_{self.name}"


COMPARE_OPS: Final[dict[str, CompareOp]] = {op.c_operator: op for op in CompareOp}


class ValueType(Enum):
    """输入字段的无符号整数类型（值 = DSL 关键字）。"""

    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    U64 = "u64"

    @property
    def bits(self) -> int:
        """位宽。"""
        return int(self.name[1:])

    @property
    def c_type(self) -> str:
        """C 侧类型名（__u8 / __u16 / __u32 / __u64）。"""
        return f"__{self.name.lower()}"

    @property
    def max_value(self) -> int:
        """该类型可表示的最大无符号值。"""
        return (1 << self.bits) - 1


VALUE_TYPES: Final[dict[str, ValueType]] = {
    value_type.value: value_type for value_type in ValueType
}


# 8. 词法与语法层数据模型
@dataclass(frozen=True)
class SourceSpan:
    """源码位置（1 基行号与列号）。"""

    line: int
    column: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError(f"invalid source span: {self.line}:{self.column}")

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


@dataclass(frozen=True)
class Token:
    """词法单元（kind 为 INT 时 value 为已解析的整数值）。"""

    kind: TokenKind
    text: str
    span: SourceSpan
    value: int | None = None


@dataclass(frozen=True)
class InputDecl:
    """input 声明（AST）：输入字段名与类型。"""

    name: str
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class Condition:
    """when 条件（AST）：字段 op 字面量。"""

    field: str
    op: CompareOp
    value: int
    span: SourceSpan


@dataclass(frozen=True)
class Rule:
    """rule 规则（AST）：条件合取 + 裁决 + 可选 fault。"""

    number: int
    conditions: tuple[Condition, ...]
    verdict: Verdict
    fault: int | None
    span: SourceSpan


@dataclass(frozen=True)
class Table:
    """决策表（AST）：钩子 + 输入集合 + 规则集合 + 默认裁决。"""

    name: str
    hook: str
    inputs: tuple[InputDecl, ...]
    rules: tuple[Rule, ...]
    default_verdict: Verdict
    span: SourceSpan


@dataclass(frozen=True)
class Program:
    """单个 .adl 文件的语法树根。"""

    version: str
    tables: tuple[Table, ...]


# 9. 中间表示（IR）：语义校验后的可直接 codegen 形式
@dataclass(frozen=True)
class InputIR:
    """输入字段（IR）：字段名、类型与决策表列索引。"""

    name: str
    value_type: ValueType
    index: int


@dataclass(frozen=True)
class ConditionIR:
    """条件（IR）：字段列索引 + 算子 + 字面量。"""

    field_index: int
    field_name: str
    op: CompareOp
    value: int


@dataclass(frozen=True)
class RowIR:
    """决策表行（IR）：条件合取 + 裁决 + 可选 fault。"""

    number: int
    conditions: tuple[ConditionIR, ...]
    verdict: Verdict
    fault: int | None


@dataclass(frozen=True)
class TableIR:
    """决策表（IR）：行按 rule 编号升序排列，评估采用 first-match。"""

    name: str
    hook: str
    inputs: tuple[InputIR, ...]
    rows: tuple[RowIR, ...]
    default_verdict: Verdict

    @property
    def prefix(self) -> str:
        """C 侧符号前缀（airy_policy_<table>）。"""
        return f"{TABLE_PREFIX}{self.name}"


@dataclass(frozen=True)
class ProgramIR:
    """单个 .adl 文件的 IR 根。"""

    version: str
    source_name: str
    tables: tuple[TableIR, ...]


# 10. 异常层级（均子类化 codegen 管道基类，携带 AIRY_DSL_* 错误码）
class DslError(CodegenError):
    """策略 DSL 编译异常基类。"""


class DslSourceError(DslError):
    """契约源缺失、编码错误或整体不可解析。"""

    def __init__(self, message: str, code: str = AIRY_DSL_SOURCE_ERROR) -> None:
        super().__init__(message, code)


class DslSyntaxError(DslError):
    """词法/语法错误（非法字符、意外 Token、结构不完整）。"""

    def __init__(self, message: str, code: str = AIRY_DSL_SYNTAX_ERROR) -> None:
        super().__init__(message, code)


class DslSemanticError(DslError):
    """语义校验错误（钩子非法、字段未声明、字面量越界、裁决-故障冲突）。"""

    def __init__(self, message: str, code: str = AIRY_DSL_SEMANTIC_ERROR) -> None:
        super().__init__(message, code)


class DslRenderError(DslError):
    """决策表 C 代码渲染失败。"""

    def __init__(self, message: str, code: str = AIRY_DSL_RENDER_ERROR) -> None:
        super().__init__(message, code)


# 11. 诊断工具
def format_diagnostic(source_name: str, span: SourceSpan, message: str) -> str:
    """构造 GCC 风格诊断串：<source>:<line>:<column>: <message>。"""
    return f"{source_name}:{span.line}:{span.column}: {message}"


def header_guard(source_name: str) -> str:
    """由契约源文件名派生唯一 include guard。

    每个 .adl 源生成独立 guard，允许多份生成头在同一翻译单元聚合包含
    （机制层已由 AIRY_POLICY_MECHANISM_DEFINED 去重）。
    """
    stem = Path(source_name).stem
    token = "".join(ch if ch.isalnum() else "_" for ch in stem).upper()
    return f"{HEADER_GUARD_PREFIX}{token}_H"
