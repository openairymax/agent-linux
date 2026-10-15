# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/airy-checkpatch 集成测试共享夹具。

被测对象是 kernel 子模块内的 Perl 规则引擎
`kernel/scripts/airy_checkpatch_ext.pl`。夹具只提供内核根路径与 perl
可执行文件，不复制任何规则逻辑。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

KERNEL_ROOT = Path(__file__).resolve().parents[3] / "kernel"


@pytest.fixture(scope="session")
def kernel_root() -> Path:
    """kernel 子模块根（引擎默认扫描范围的锚点）。"""
    return KERNEL_ROOT


@pytest.fixture(scope="session")
def perl() -> str:
    """perl 解释器路径；缺失时跳过整个套件。"""
    exe = shutil.which("perl")
    if exe is None:
        pytest.skip("perl 不可用，跳过 airy-checkpatch 集成测试")
    return exe
