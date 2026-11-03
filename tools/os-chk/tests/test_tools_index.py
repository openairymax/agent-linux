# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/README.md 聚合清单与磁盘的一致性守卫。

聚合 README 是「工具在哪儿、谁在用」的唯一入口。没有守卫时它只会单向
落后：新增 airy-qemu 与 os-chk 之后，目录树与工具表长期缺项，读者据此
判断「有哪些工具」就会漏掉整个农场与门禁编排器；反向更糟——工具删了而
表里还在，新人会照着一条死路径排查。

因此本测试以磁盘为准做双向校验：每个工具目录都必须在 README 里出现，
每条登记的工具路径都必须真实存在。CI-Referenced Helper Surface 表
按设计登记尚未落地的脚本，不参与反向校验。

命令面另有一层：`tools/airy` 只按 commands.tsv 分发，工具表的每个脚本
都必须在那张表里有命令名，否则「有工具但叫不出命令」；表里每条路径也
必须真实存在，否则分发器指向死文件。二者同样双向校验。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
README = TOOLS / "README.md"
TABLE = TOOLS / "commands.tsv"
AIRY = TOOLS / "airy"

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


def commands() -> list[tuple[str, str]]:
    """commands.tsv 的 (命令名, 脚本路径)，注释与空行不参与。"""
    rows = []
    for raw in TABLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, path, _ = line.split(None, 2)
        rows.append((name, path))
    return rows


def airy(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(AIRY), *args],
                          text=True, capture_output=True)


class TestCommandSurface:
    def test_table_exists(self) -> None:
        assert TABLE.is_file(), "tools/commands.tsv 缺失：命令面无唯一声明"

    def test_names_follow_convention(self) -> None:
        bad = [name for name, _ in commands()
               if not re.fullmatch(r"[a-z][a-z0-9-]*", name)]
        assert not bad, f"命令名须为小写 kebab-case：{bad}"

    def test_commands_are_unique(self) -> None:
        names = [name for name, _ in commands()]
        dup = sorted({name for name in names if names.count(name) > 1})
        assert not dup, f"命令名重复：{dup}"

    def test_every_command_resolves(self) -> None:
        missing = [f"{name} → {path}" for name, path in commands()
                   if not (TOOLS / path).is_file()]
        assert not missing, f"命令指向不存在的脚本：{missing}"

    def test_catalog_and_table_agree(self) -> None:
        """工具表的入口脚本与命令表必须一一对应。

        缺命令意味着工具只能用记忆中的文件名调用；多命令意味着分发器指向
        一个没在工具表解释用途的脚本。两个方向都会让命令面失真。
        """
        catalogued = {path for path in ROW.findall(catalog())
                      if path.endswith(".py")}
        declared = {path for _, path in commands()}
        assert catalogued == declared, (
            f"缺命令：{sorted(catalogued - declared)}；"
            f"未登记用途：{sorted(declared - catalogued)}")

    def test_list_reports_every_command(self) -> None:
        res = airy("--list")
        assert res.returncode == 0, res.stderr
        absent = [name for name, _ in commands()
                  if name not in res.stdout.split()]
        assert not absent, f"--list 漏列命令：{absent}"

    def test_help_succeeds(self) -> None:
        res = airy("--help")
        assert res.returncode == 0
        assert "用法" in res.stdout

    def test_unknown_command_rejected(self) -> None:
        res = airy("no-such-command")
        assert res.returncode == 2
        assert "--list" in res.stderr
