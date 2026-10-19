# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt 单元测试共享夹具。

把 tools/osaccrt 注入 sys.path，使测试以 `import osaccrt_model` 绝对
导入被测模块。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

OSACCRT_DIR = Path(__file__).resolve().parents[1]

if str(OSACCRT_DIR) not in sys.path:
    sys.path.insert(0, str(OSACCRT_DIR))


@pytest.fixture(scope="session")
def osaccrt_dir() -> Path:
    """tools/osaccrt 目录（执行器宿主）。"""
    return OSACCRT_DIR
