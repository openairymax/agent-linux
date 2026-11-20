#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/modmap_conform.py — 模块入口映射的文档与代码一致性检查
#
# system/tui/modmap.toml 是人读的册子，system/tui/crates/airy-adapters/src/
# modmap.rs 是同一份登记在会话层的投影。两份文件各自有用：册子进文档评审，
# 投影进编译单元。只要允许只改一侧，两个读者迟早看到不同的十九个模块，
# 因此本检查逐字段比对，任何一处不等即判负。
#
# 投影侧不按行解析：`cover(...)` 与 `defer(...)` 的换行由 rustfmt 决定，
# 按行匹配会把格式改动误判成登记缺失。这里先取出调用名与实参，再按调用名
# 补齐七列——deferred 的入口与两条判据固定为 `-`，与 TOML 侧同口径。
#
# 代号在投影里写作 `stringify!(L)` 而不是字面量 `"L"`：一行一条的登记表若
# 全靠字符串区分，去字符串后的行就只剩逗号，重复率棘轮会把这张表判成复制
# 粘贴。标识符留在行内，两侧仍逐字段相等，只是取值方式不同。
#
# 除比对之外还判三件事：代号十九个且不重复；covered 的入口在仓库内真实存在
# 且成功、失败两条判据都写了；deferred 的入口固定为 `-` 且 note 首段写明
# 承接版本——留白与「下次再说」都不合法。
#
# 用法: modmap_conform.py [--root DIR]
# 退出码: 0 一致；1 不一致或不合规；5 用法或环境错误。
# ===========================================================================

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

CODES = {
    "L", "A", "M", "B", "F", "J", "E", "C", "D", "I",
    "G", "K", "N", "H", "O", "P", "Q", "R1", "R2",
}
FIELDS = ("code", "name", "scope", "entry", "ok", "fail", "note")
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
CALL = re.compile(
    r"\b(cover|defer)\s*\(((?:stringify!\s*\(\s*" + IDENT + r"\s*\)|[^()])*)\)", re.S)
ARG = re.compile(r"stringify!\s*\(\s*(" + IDENT + r")\s*\)|\"([^\"]*)\"")
VERSION = re.compile(r"^0\.[01]\.")

EXIT_OK = 0
EXIT_BAD = 1
EXIT_USAGE = 5


def say(msg: str) -> None:
    """打印一条判定。"""
    print(f"modmap: {msg}")


def load_toml(path: Path) -> list[dict[str, str]]:
    """读册子侧的十九行。"""
    with open(path, "rb") as stream:
        raw = tomllib.load(stream)
    rows = []
    for item in raw.get("module", []):
        row = {k: str(item.get(k, "")) for k in FIELDS}
        rows.append(row)
    return rows


def args_of(body: str) -> list[str]:
    """一次调用的实参：引号字面量取内容，`stringify!(X)` 取标识符。"""
    return [m.group(1) if m.group(1) is not None else m.group(2)
            for m in ARG.finditer(body)]


def row_of(kind: str, args: list[str]) -> dict[str, str] | None:
    """把一次登记调用摊成七列；实参个数不符返回 None。"""
    row = {k: "" for k in FIELDS}
    if kind == "cover" and len(args) == 6:
        row.update(zip(("code", "name", "entry", "ok", "fail", "note"), args))
        row["scope"] = "covered"
        return row
    if kind == "defer" and len(args) == 3:
        row.update(zip(("code", "name", "note"), args))
        row.update(scope="deferred", entry="-", ok="-", fail="-")
        return row
    return None


def load_rust(path: Path) -> list[dict[str, str]]:
    """从投影里取出同样七列。"""
    text = path.read_text(encoding="utf-8")
    rows = []
    for kind, body in CALL.findall(text):
        row = row_of(kind, args_of(body))
        if row is not None:
            rows.append(row)
    return rows


def codes_bad(rows: list[dict[str, str]]) -> list[str]:
    """判十九个代号齐、不重、无外来。"""
    bad = []
    codes = [r["code"] for r in rows]
    if len(rows) != len(CODES):
        bad.append(f"登记 {len(rows)} 条，应为 {len(CODES)} 条")
    if len(set(codes)) != len(codes):
        bad.append("代号重复")
    if set(codes) != CODES:
        bad.append(f"代号集合不符：缺 {sorted(CODES - set(codes))}，多 {sorted(set(codes) - CODES)}")
    return bad


def covered_bad(row: dict[str, str], root: Path) -> list[str]:
    """判 covered 的入口与两条判据。"""
    bad = []
    if row["ok"] == "-" or row["fail"] == "-":
        bad.append(f"{row['code']}: covered 缺判据")
    if row["entry"] == "-" or not (root / row["entry"]).exists():
        bad.append(f"{row['code']}: 入口不存在 {row['entry']}")
    return bad


def deferred_bad(row: dict[str, str]) -> list[str]:
    """判 deferred 不留入口、承接版本写在 note 首段。"""
    bad = []
    if row["entry"] != "-":
        bad.append(f"{row['code']}: deferred 不应有入口")
    if not VERSION.match(row["note"]):
        bad.append(f"{row['code']}: deferred 未写承接版本")
    return bad


def row_bad(row: dict[str, str], root: Path) -> list[str]:
    """按覆盖状态分派单行判据。"""
    if row["scope"] == "covered":
        return covered_bad(row, root)
    if row["scope"] == "deferred":
        return deferred_bad(row)
    return [f"{row['code']}: 覆盖状态非法 {row['scope']!r}"]


def check(rows: list[dict[str, str]], root: Path) -> list[str]:
    """判十九行自洽：代号、覆盖三列、承接版本。"""
    bad = codes_bad(rows)
    for row in rows:
        bad.extend(row_bad(row, root))
    return bad


def diff(left: list[dict[str, str]], right: list[dict[str, str]]) -> list[str]:
    """逐字段比对册子与投影。"""
    if len(left) != len(right):
        return [f"册子 {len(left)} 条，投影 {len(right)} 条"]
    bad = []
    for a, b in zip(left, right):
        for key in FIELDS:
            if a[key] != b[key]:
                bad.append(f"{a['code']}.{key}: 册子与投影不一致")
    return bad


def main() -> int:
    """入口：比对册子与投影，再判十九行自洽。"""
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--root", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    book = root / "system/tui/modmap.toml"
    proj = root / "system/tui/crates/airy-adapters/src/modmap.rs"
    for need in (book, proj):
        if not need.exists():
            say(f"缺少 {need}")
            return EXIT_USAGE
    left, right = load_toml(book), load_rust(proj)
    bad = check(left, root) + diff(left, right)
    if bad:
        for line in bad:
            say(line)
        return EXIT_BAD
    covered = sum(1 for r in left if r["scope"] == "covered")
    say(f"{len(left)} 条登记一致（covered {covered} / deferred {len(left) - covered}）")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
