# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl 单元测试共享夹具。

把 tools/dsl 与其平级的 tools/codegen 注入 sys.path：前者提供 DSL 编译器
模块（以 `import dsl_types` 等绝对导入），后者提供被复用的 codegen 管道
基础设施（codegen_types / codegen_io）。同时提供 agent-linux 仓库根夹具，
供零漂移校验读取真实契约源与已提交产物。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DSL_DIR = Path(__file__).resolve().parents[1]
CODEGEN_DIR = DSL_DIR.parent / "codegen"
REPO_ROOT = Path(__file__).resolve().parents[3]

for _path in (DSL_DIR, CODEGEN_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture(scope="session")
def dsl_dir() -> Path:
    """tools/dsl 目录（DSL 编译器与模板宿主机）。"""
    return DSL_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """agent-linux/ 仓库根（真实契约源与生成产物宿主）。"""
    return REPO_ROOT
