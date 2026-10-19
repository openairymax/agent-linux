# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""airy-qemu 单元测试共享夹具。

把 tools/airy-qemu 注入 sys.path，使测试以 `import farm_types` 绝对导入
共享类型模块；同时提供 agent-linux 仓库根夹具，供后续分片读取真实 CI
工作流与 kselftest 安装布局。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FARM_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(FARM_DIR) not in sys.path:
    sys.path.insert(0, str(FARM_DIR))


@pytest.fixture(scope="session")
def farm_dir() -> Path:
    """tools/airy-qemu 目录（农场工具宿主）。"""
    return FARM_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """agent-linux/ 仓库根（CI 工作流与内核源码宿主）。"""
    return REPO_ROOT
