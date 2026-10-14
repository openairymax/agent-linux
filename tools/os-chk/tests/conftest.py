# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""os-chk 单元测试共享夹具。

把 tools/os-chk 注入 sys.path，使测试可 `import os_chk`（该脚本无包前缀，
直接以绝对导入加载）。同时提供 agent-linux 仓库根夹具，供端到端门禁
执行读取真实注册表 gates.yaml 与既有执行器。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
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
    """tools/os-chk 目录（脚本宿主）。"""
    return TOOL_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """agent-linux/ 仓库根（注册表与既有执行器宿主）。"""
    return REPO_ROOT
