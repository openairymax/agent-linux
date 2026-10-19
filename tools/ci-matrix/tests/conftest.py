# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/ci-matrix 测试夹具：把工具目录加入 import 路径。"""

from __future__ import annotations

import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))
