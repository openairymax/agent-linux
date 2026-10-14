# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""abi-check 单元测试共享夹具。

把 tools/abi-check 注入 sys.path，使测试可 `import abi_check`
（该脚本无包前缀，直接以绝对导入加载）。同时提供 agent-linux 仓库根
夹具，供端到端检查读取真实四方权威源（SSoT / unistd.h /
syscall_64.tbl / [SC] syscalls.h）与基线快照。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))


@pytest.fixture(scope="session")
def tool_dir() -> Path:
    """tools/abi-check 目录（脚本宿主）。"""
    return TOOL_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """agent-linux/ 仓库根（四方权威源与基线宿主）。"""
    return REPO_ROOT
