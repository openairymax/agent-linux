# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""codegen_types — agent-linux 代码生成管道共享接口与类型。

集中声明契约版本常量、命名空间前缀、槽位与布局常量、生成器入口契约、
退出码、异常层级，以及 syscall 契约源（R-01 SSoT）的数据模型。四个生成器
（cap_gen / bitfield_gen / ipc_gen / syscall_gen）与统一入口 run_codegen
统一 import 本模块，消除跨模块的类型重复定义。

本模块的数据模型均为真实可用的 frozen dataclass（构造时经 __post_init__
校验），不含任何占位或桩逻辑。

Example:
    >>> from codegen_types import GeneratorName, LINUX_BASE
    >>> GeneratorName.SYSCALL.value
    'syscall_gen'
    >>> LINUX_BASE
    548

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Callable, TypeAlias

# 2. 公开导出
__all__ = [
    "CAP_EXTENSION_BASE",
    "CAP_ID_COUNT",
    "CAP_ID_MAX_SYMBOL",
    "CAP_ID_PREFIX",
    "CAP_SYMBOL_PREFIX",
    "CORE_SYSCALL_COUNT",
    "FUNCTION_PREFIX",
    "GENERATOR_VERSION",
    "INDEX_PREFIX",
    "IPC_HEADER_ALIGNMENT",
    "IPC_HEADER_SIZE",
    "IPC_MAGIC",
    "LINUX_BASE",
    "PIPELINE_ORDER",
    "RESERVED_SLOT_COUNT",
    "SCHEMA_VERSION",
    "SYMBOL_PREFIX",
    "TOTAL_SLOT_COUNT",
    "CapContract",
    "CapIdSpec",
    "CodegenError",
    "ContractSourceError",
    "ContractValidationError",
    "ExitCode",
    "GeneratedDriftError",
    "GeneratorEntry",
    "GeneratorName",
    "ReservedRange",
    "SyscallContract",
    "SyscallSpec",
    "TemplateRenderError",
]

# 3. 契约版本与命名空间常量
GENERATOR_VERSION = "1.0.1"
SCHEMA_VERSION = "1.0"
SYMBOL_PREFIX = "AIRY_SYS_"
INDEX_PREFIX = "AIRY_SYS_IDX_"
FUNCTION_PREFIX = "airy_sys_"

# 4. syscall 槽位常量（v1.0.1 Capability Folding）
LINUX_BASE = 548
CORE_SYSCALL_COUNT = 4
RESERVED_SLOT_COUNT = 20
TOTAL_SLOT_COUNT = 24

# 5. IPC 消息头布局常量（contracts.md §2 Layout C v4）
IPC_HEADER_SIZE = 128
IPC_HEADER_ALIGNMENT = 64
IPC_MAGIC = 0x41524531  # 'ARE1'

# 6. capability 常量（41 POSIX + 3 Airymax 专属）
CAP_ID_COUNT = 44

# 7. capability 命名空间常量（D-9 前缀转换 + [SC] enum 生成）
CAP_EXTENSION_BASE = 41
CAP_ID_PREFIX = "AIRY_"
CAP_SYMBOL_PREFIX = "AIRY_CAP"
CAP_ID_MAX_SYMBOL = "AIRY_CAP_ID_MAX"

# 8. 生成器入口契约：run_codegen 依此签名调用每个生成器
GeneratorEntry: TypeAlias = Callable[[Path], int]


class GeneratorName(Enum):
    """codegen 管道 4 个生成器的模块名。"""

    CAP = "cap_gen"
    BITFIELD = "bitfield_gen"
    IPC = "ipc_gen"
    SYSCALL = "syscall_gen"


class ExitCode(IntEnum):
    """生成器入口返回码（run_codegen 依此判定成败）。"""

    SUCCESS = 0
    FAILURE = 1


PIPELINE_ORDER: tuple[GeneratorName, ...] = (
    GeneratorName.CAP,
    GeneratorName.BITFIELD,
    GeneratorName.IPC,
    GeneratorName.SYSCALL,
)


class CodegenError(Exception):
    """codegen 管道异常基类（message + code 模式）。"""

    def __init__(self, message: str, code: str = "AIRY_CODEGEN_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ContractSourceError(CodegenError):
    """契约源缺失或不可解析（文件不存在、XML / .bf / .def 语法错误）。"""


class ContractValidationError(CodegenError):
    """契约源语义校验失败（槽位不连续、符号未定义、跨契约布局不一致）。"""


class TemplateRenderError(CodegenError):
    """Jinja2 模板渲染失败。"""


class GeneratedDriftError(CodegenError):
    """生成产物与已提交文件不一致（CI 零 diff 校验失败）。"""


@dataclass(frozen=True)
class ReservedRange:
    """预留槽位区间（syscall.xml <reserved> 元素）。"""

    start: int
    end: int
    reason: str = "future extension"

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ContractValidationError(f"invalid reserved range: {self.start}..{self.end}")

    @property
    def count(self) -> int:
        """区间内槽位数量（含两端）。"""
        return self.end - self.start + 1


@dataclass(frozen=True)
class SyscallSpec:
    """单个 syscall 契约条目（syscall.xml <syscall> 元素）。"""

    name: str
    number: int
    signature: str
    flags: str
    category: str
    since: str
    stability: str
    doc: str
    capability_required: str = ""

    def __post_init__(self) -> None:
        if not self.name.startswith(FUNCTION_PREFIX):
            raise ContractValidationError(f"syscall name lacks {FUNCTION_PREFIX} prefix: {self.name}")
        if self.number < 0:
            raise ContractValidationError(f"negative syscall number: {self.number}")

    @property
    def suffix(self) -> str:
        """去除函数名前缀后的大写后缀（CALL / ROVOL_CTL ...）。"""
        return self.name[len(FUNCTION_PREFIX):].upper()

    @property
    def symbol_macro(self) -> str:
        """外部符号宏（AIRY_SYS_CALL，对应实际 syscall 号命名空间）。"""
        return SYMBOL_PREFIX + self.suffix

    @property
    def index_macro(self) -> str:
        """codegen 内部索引宏（AIRY_SYS_IDX_CALL，与 syscalls.h 命名空间分离）。"""
        return INDEX_PREFIX + self.suffix

    def linux_nr(self, linux_base: int = LINUX_BASE) -> int:
        """Linux 注册号（内部编号 + linux_base）。"""
        return self.number + linux_base

    def linux_nr_macro(self) -> str:
        """Linux 注册号宏名（__NR_<name>）。"""
        return f"__NR_{self.name}"


@dataclass(frozen=True)
class SyscallContract:
    """syscall.xml 契约源整体模型（R-01 SSoT）。"""

    version: str
    linux_base: int
    core_count: int
    reserved_count: int
    total_slots: int
    syscalls: tuple[SyscallSpec, ...]
    reserved: tuple[ReservedRange, ...]
    generator: str = "syscall_gen.py"
    generator_version: str = GENERATOR_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.core_count != len(self.syscalls):
            raise ContractValidationError("core_count does not match syscall entry count")
        if self.reserved_count != sum(r.count for r in self.reserved):
            raise ContractValidationError("reserved_count does not match reserved ranges")
        if self.total_slots != self.core_count + self.reserved_count:
            raise ContractValidationError("total_slots != core_count + reserved_count")
        if sorted(sc.number for sc in self.syscalls) != list(range(self.core_count)):
            raise ContractValidationError("syscall numbers must be contiguous from 0")

    @property
    def syscalls_by_number(self) -> dict[int, SyscallSpec]:
        """按内部编号索引的 syscall 映射。"""
        return {sc.number: sc for sc in self.syscalls}


@dataclass(frozen=True)
class CapIdSpec:
    """单个 capability ID 契约条目（airy_cap_ids.def 的 AIRY_CAP_ID 行）。"""

    symbol: str
    value: int
    description: str
    raw_symbol: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.startswith(CAP_SYMBOL_PREFIX + "_"):
            raise ContractValidationError(
                f"capability symbol lacks {CAP_SYMBOL_PREFIX}_ prefix: {self.symbol}"
            )
        if not 0 <= self.value < CAP_ID_COUNT:
            raise ContractValidationError(f"capability id out of range: {self.value}")
        if not self.description:
            raise ContractValidationError(f"capability {self.symbol} has empty description")

    @property
    def is_extension(self) -> bool:
        """是否为 Airymax 专属扩展（value >= CAP_EXTENSION_BASE）。"""
        return self.value >= CAP_EXTENSION_BASE

    @property
    def variant(self) -> str:
        """Rust 枚举变体名（去掉 AIRY_CAP_ 前缀后转 PascalCase）。"""
        stem = self.symbol[len(CAP_SYMBOL_PREFIX) + 1:]
        return "".join(part.capitalize() for part in stem.split("_"))


@dataclass(frozen=True)
class CapContract:
    """airy_cap_ids.def 契约源整体模型（Capability ID SSoT）。"""

    cap_ids: tuple[CapIdSpec, ...]
    generator: str = "cap_gen.py"
    generator_version: str = GENERATOR_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(self.cap_ids) != CAP_ID_COUNT:
            raise ContractValidationError(
                f"capability id count {len(self.cap_ids)} != {CAP_ID_COUNT}"
            )
        if sorted(c.value for c in self.cap_ids) != list(range(CAP_ID_COUNT)):
            raise ContractValidationError("capability ids must be contiguous from 0")

    @property
    def cap_ids_by_value(self) -> dict[int, CapIdSpec]:
        """按数值索引的 capability 映射。"""
        return {c.value: c for c in self.cap_ids}

    @property
    def posix_caps(self) -> tuple[CapIdSpec, ...]:
        """POSIX 标准 capability（value 0..CAP_EXTENSION_BASE-1）。"""
        return tuple(c for c in self.cap_ids if not c.is_extension)

    @property
    def extension_caps(self) -> tuple[CapIdSpec, ...]:
        """Airymax 专属 capability（value >= CAP_EXTENSION_BASE）。"""
        return tuple(c for c in self.cap_ids if c.is_extension)

    @property
    def symbol_names(self) -> frozenset[str]:
        """全部 capability C 枚举符号集合（§5.4 跨契约校验用）。"""
        return frozenset(c.symbol for c in self.cap_ids)

    @property
    def symbol_width(self) -> int:
        """enum 对齐宽度（最长符号 + 1）。"""
        return max(len(c.symbol) for c in self.cap_ids) + 1
