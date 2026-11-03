# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/README.md 聚合清单与磁盘的一致性守卫。

聚合 README 是「工具在哪儿、谁在用」的唯一入口。没有守卫时它只会单向
落后：新增 airy-qemu 与 os-chk 之后，目录树与工具表长期缺项，读者据此
判断「有哪些工具」就会漏掉整个农场与门禁编排器；反向更糟——工具删了而
表里还在，新人会照着一条死路径排查。

因此本测试以磁盘为准做双向校验：每个工具目录都必须在 README 里出现，
每条登记的工具路径都必须真实存在。CI-Referenced Helper Surface 表
按设计登记尚未落地的脚本，不参与反向校验。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import re
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
README = TOOLS / "README.md"

CATALOG_HEAD = "### Implemented"
CATALOG_TAIL = "### CI-Referenced Helper Surface"

# 工具表行形如 `| \`codegen/run_codegen.py\` | ... |`。
ROW = re.compile(r"^\|\s*`([A-Za-z0-9_./-]+)`\s*\|", re.MULTILINE)


def tool_dirs() -> list[str]:
    """返回带自有 README 的工具目录名（排序，便于失败信息稳定）。

    以 `.`、`_` 开头的目录是本地缓存与构建残留（`.pytest_cache`、
    `__pycache__`），不是工具，不参与清单校验。
    """
    names = [
        path.name
        for path in TOOLS.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and (path / "README.md").is_file()
    ]
    return sorted(names)


def catalog() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index(CATALOG_HEAD)
    return text[start:text.index(CATALOG_TAIL, start)]


class TestToolsIndex:
    def test_readme_exists(self) -> None:
        assert README.is_file()

    def test_every_tool_dir_is_documented(self) -> None:
        text = README.read_text(encoding="utf-8")
        missing = [name for name in tool_dirs() if f"{name}/" not in text]
        assert not missing, f"tools/README.md 未登记工具目录：{missing}"

    def test_every_catalog_path_exists(self) -> None:
        missing = [
            path for path in ROW.findall(catalog())
            if not (TOOLS / path).exists()
        ]
        assert not missing, f"tools/README.md 登记了不存在的工具：{missing}"
