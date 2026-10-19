#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
rollback_types.py — airy-rollback 回滚工具的类型与接口契约

本模块是 airy-rollback 的接口层：只定义回滚层次、快照元数据、执行
步骤、校验结果、清单（manifest）数据模型与命令执行器接口，不含编排
逻辑。编排逻辑位于 airy_rollback.py。

权威依据：
  docs/docs-linux/100-operations/01-deployment.md §11（L1/L2/L3、
    OS-OPS-021/022/023/036）
  docs/docs-linux/120-development-process/09-release-process.md §7.2-7.5
  docs/docs-linux/100-operations/07-systemd-integration.md §8.1（12 unit）
  docs/docs-linux/50-engineering-standards/10-coding-style/
    scripting_coding_style.md（类型注解、数据类、错误码到异常）

设计约束：
  * 数据类型全部不可变，跨模块共享同一份定义（Unify Design SSoT）。
  * 外部命令模板存放在 manifest.yaml，代码只做编排，避免双事实源。
  * 错误以 RollbackError 携带进程退出码，禁止散落 sys.exit()。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

__all__ = [
    "EXIT_BUSY",
    "EXIT_FAIL",
    "EXIT_INTEGRITY",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_PREFLIGHT",
    "EXIT_TARGET",
    "EXIT_USAGE",
    "EXIT_VERIFY",
    "Check",
    "Kind",
    "Layer",
    "Manifest",
    "Outcome",
    "Plan",
    "Proc",
    "RollbackError",
    "Runner",
    "Snapshot",
    "Step",
]

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_PREFLIGHT = 3
EXIT_TARGET = 4
EXIT_INTEGRITY = 5
EXIT_VERIFY = 6
EXIT_BUSY = 7
EXIT_PARTIAL = 8


class Layer(str, Enum):
    """回滚层次（01-deployment.md §11.1）。

    AUTO 表示由工具按快照元数据与当前运行态选择最浅的充分层次。
    """

    AUTO = "auto"
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"


class Kind(str, Enum):
    """L3 根分区快照后端（OS-OPS-021：btrfs 或 LVM 二选一）。"""

    BTRFS = "btrfs"
    LVM = "lvm"


@dataclass(frozen=True)
class Proc:
    """外部命令执行结果。

    Attributes:
        code: 进程退出码；执行器自身故障使用 124（超时）或 127（不可用）。
        out: 标准输出（已去除首尾空白）。
        err: 标准错误（已去除首尾空白）。
    """

    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        """命令是否以 0 退出。"""
        return self.code == 0


@dataclass(frozen=True)
class Snapshot:
    """L3 快照及其元数据（<snapshot>/rollback.json）。

    Attributes:
        path: 快照目录（btrfs 子卷路径或 LVM 快照标识所在目录）。
        version: 快照对应的 AirymaxOS 版本。
        created: ISO-8601 UTC 创建时间，用于选择最新快照。
        kind: 快照后端类型。
        source: btrfs 子卷路径或 LVM 逻辑卷名，交给回滚命令模板。
        kernel: 快照时刻的内核 release，用于层次决策。
        daemons: 快照时刻的 daemon unit 集合，用于层次决策。
        dnf_tx: 可选的 dnf 事务 ID；存在时允许 L1 回滚。
    """

    path: Path
    version: str
    created: str
    kind: Kind
    source: str
    kernel: str
    daemons: tuple[str, ...]
    dnf_tx: int | None = None


@dataclass(frozen=True)
class Step:
    """回滚计划中的一个可执行步骤。

    Attributes:
        argv: 已展开模板的命令参数列表。
        note: 面向操作者的步骤说明（写入审计与计划输出）。
    """

    argv: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class Plan:
    """回滚计划：层次、目标、步骤与选择依据。

    Attributes:
        layer: 实际执行的层次（不会是 AUTO）。
        target: 目标版本。
        steps: 按序执行的命令。
        reason: 层次选择依据，供审计与人工复核。
    """

    layer: Layer
    target: str
    steps: tuple[Step, ...]
    reason: str


@dataclass(frozen=True)
class Check:
    """单项校验结果。"""

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Outcome:
    """一次命令的最终结果（退出码 + 证据）。"""

    code: int
    detail: str
    plan: Plan | None = None
    checks: tuple[Check, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """回滚工具契约清单（manifest.yaml 的运行时视图）。

    Attributes:
        version: 清单版本。
        release_file: 当前版本标识文件。
        snapshot_root: L3 快照根目录。
        audit_log: 回滚审计日志（JSONL，append-only）。
        pending_flag: 升级进行中标记（UPD 的 .pending-upgrade）。
        lock_file: 回滚互斥锁文件。
        memory_path: Agent 记忆卷挂载点（OS-OPS-036）。
        daemons: 12 个 daemon unit（OS-OPS-022）。
        commands: 命令模板；占位符由代码在计划阶段展开。
        retention_days: L3 快照保留天数（OS-OPS-021）。
        timeout_s: 单条外部命令超时。
    """

    version: str
    release_file: Path
    snapshot_root: Path
    audit_log: Path
    pending_flag: Path
    lock_file: Path
    memory_path: Path
    daemons: tuple[str, ...]
    commands: Mapping[str, tuple[str, ...]]
    retention_days: int
    timeout_s: float = 30.0


class RollbackError(Exception):
    """带进程退出码的回滚错误。

    错误码到退出码的映射集中在异常构造处，避免 sys.exit() 散落。

    Attributes:
        code: 进程退出码（EXIT_*）。
        detail: 面向操作者的中文诊断。
    """

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Runner(Protocol):
    """外部命令执行器接口。

    生产实现基于 subprocess；测试注入受控执行器以验证编排与错误路径。
    """

    def run(self, argv: Sequence[str], timeout_s: float) -> Proc:
        """执行命令并返回结果。

        Args:
            argv: 命令与参数。
            timeout_s: 超时秒数。

        Returns:
            执行结果；执行器故障以非零退出码表达，不抛出异常。
        """
        ...
