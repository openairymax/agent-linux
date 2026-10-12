# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""codegen 单元测试共享夹具。

把 tools/codegen 注入 sys.path，使测试可直接 import 生成器模块（各生成器
以 `import codegen_io` / `from codegen_types import ...` 做绝对导入），并
提供 agent-linux 仓库根夹具。四生成器与统一入口 run_codegen 的测试共用。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CODEGEN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(CODEGEN_DIR) not in sys.path:
    sys.path.insert(0, str(CODEGEN_DIR))


@pytest.fixture(scope="session")
def codegen_dir() -> Path:
    """tools/codegen 目录（生成器与模板宿主机）。"""
    return CODEGEN_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """agent-linux/ 仓库根（真实契约源与生成产物宿主）。"""
    return REPO_ROOT
