#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/tui_isolation.py — 双通路依赖隔离的判定实现（§2.2 铁律①）
#
# 两条 TUI 通路共享契约、不共享代码：引导期 TUI 本地执行、不链 agentrt；
# GUI dock 经 AirymaxRT gateway。这条边界只能靠依赖图守，靠人看代码迟早
# 失守——一条 `agentrt-core = "..."` 就能把引导期 TUI 变成运行时客户端，
# 而编译、测试与 clippy 全都不会报错。
#
# 判据三条，都在 Cargo.toml 上静态判定，不跑 cargo（门禁不该要求宿主装
# 工具链，也不该为一次判定付整树编译的代价）：
#
#   T001 正向：TUI 工作区的任何依赖表里出现 `agentrt` 或 `agentrt-*`。
#        含 dev 与 build 依赖：测试脚手架拿到运行时句柄，等于通路已连。
#   T002 路径越界：`path` 依赖解析后落在本侧根之外。绕过 crates.io 名字
#        检查的最短路径就是 `path = "../../agentrt/core"`。
#   T003 反向：agentrt 树依赖任何 TUI crate，或路径指进 TUI 根。多仓开发
#        机自动判；单仓检出看不到对侧，打印一句「反向未判」而不是假装通过。
#
# 只扫工作区自有清单：vendor/ 是第三方快照，target/ 是构建产物，两者都不
# 适用本判据。
#
# 用法: python3 ci/checks/tui_isolation.py [--agentrt DIR] [根...]
#       根缺省 system/tui；--agentrt 缺省取同级的 agent-workload/agentrt，
#       也可由环境变量 AGENTRT_DIR 指定。
# 退出码: 0 无交叉边；1 有交叉或清单不可解析；5 参数不满足。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================
from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import cli

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ("system/tui",)
SKIP = frozenset(("vendor", "target", ".git"))
RUNTIME = re.compile(r"^agentrt(?:[-_].*)?$")
TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def manifests(base: Path) -> list[Path]:
    """工作区自有清单：vendor/ 与 target/ 是第三方快照与构建产物。"""
    return [p for p in base.rglob("Cargo.toml") if SKIP.isdisjoint(p.parts)]


def loaded(base: Path) -> list[tuple[Path, dict]]:
    """一份清单读一次：包名与依赖表两侧都要用。"""
    return [(p, tomllib.loads(p.read_text(encoding="utf-8"))) for p in manifests(base)]


def tables(doc: dict) -> list[dict]:
    """依赖表：三类常规表加按 target 分列的表。"""
    specs = [doc, *doc.get("target", {}).values()]
    return [spec[name] for spec in specs for name in TABLES
            if isinstance(spec.get(name), dict)]


def deps(doc: dict) -> list[tuple[str, object]]:
    """摊平后的 (依赖名, 规格)。"""
    return [(name, spec) for table in tables(doc) for name, spec in table.items()]


def leak(parent: Path, spec: object, guard: Path) -> Path | None:
    """path 依赖的落点；不是 path 依赖即 None。"""
    rel = spec.get("path") if isinstance(spec, dict) else None
    return None if rel is None else (parent / str(rel)).resolve()


def inside(path: Path, base: Path) -> bool:
    """path 落在 base 之内（含 base 本身）。"""
    return path == base or base in path.parents


def rel(path: Path) -> str:
    """报告用路径：仓内取相对，夹具树取绝对。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def escapes(path: Path, doc: dict, guard: Path, inward: bool) -> list[str]:
    """path 依赖方向错位的明细；inward 为真时判「指进根内」。"""
    word = "指入" if inward else "出根"
    got = []
    for dep, spec in deps(doc):
        out = leak(path.parent, spec, guard)
        if out is not None and inside(out, guard) == inward:
            got.append(f"{rel(path)}: {dep} 路径{word} {out}")
    return got


def names_of(got: list[tuple[Path, dict]]) -> set[str]:
    """工作区的 crate 名，反向判定的依据。"""
    return {doc["package"]["name"] for _, doc in got if "name" in doc.get("package", {})}


def forward(base: Path, guard: Path) -> tuple[list[str], set[str], int]:
    """正向：本侧清单不得依赖 agentrt，也不得把 path 伸出根外。"""
    bad: list[str] = []
    got = loaded(base)
    for path, doc in got:
        bad += [f"{rel(path)}: 依赖 {dep}（双通路耦合）"
                for dep, _ in deps(doc) if RUNTIME.match(dep)]
        bad += escapes(path, doc, guard, False)
    return bad, names_of(got), len(got)


def backward(base: Path, guard: Path, crates: set[str]) -> list[str]:
    """反向：对侧清单不得依赖 TUI crate，也不得把 path 指进 TUI 根。"""
    bad: list[str] = []
    for path, doc in loaded(base):
        bad += [f"{rel(path)}: 依赖 {dep}（反向耦合）"
                for dep, _ in deps(doc) if dep in crates]
        bad += escapes(path, doc, guard, True)
    return bad


def peer_dir(opt) -> Path:
    """对侧运行时树：显式参数、环境变量、同级工作负载目录。"""
    fallback = ROOT.parent / "agent-workload" / "agentrt"
    return Path(opt.agentrt or os.environ.get("AGENTRT_DIR") or fallback)


def absent(roots: list[Path]) -> str | None:
    """首个不存在的扫描根；全在则 None。"""
    return next((str(base) for base in roots if not base.is_dir()), None)


def judge(roots: list[Path], peer: Path) -> int:
    """两侧互判并汇总。"""
    missing = absent(roots)
    if missing:
        print(f"目录不存在: {missing}", file=sys.stderr)
        return 5
    bad: list[str] = []
    crates: set[str] = set()
    count = 0
    for base in roots:
        hit, names, seen = forward(base, base)
        bad += hit
        crates |= names
        count += seen
    if peer.is_dir():
        print(f"tui-isolation: 反向已判（{peer}）", file=sys.stderr)
        bad += backward(peer.resolve(), roots[0], crates)
    else:
        print(f"tui-isolation: 未检出 agentrt 树（{peer}），反向未判", file=sys.stderr)
    for line in bad:
        print(f"tui-isolation: {line}", file=sys.stderr)
    print(f"tui-isolation: {count} 份清单，{len(crates)} 个 crate，{len(bad)} 处交叉")
    return 1 if bad else 0


def roots_of(opt) -> list[Path]:
    """扫描根：位置参数优先，缺省 system/tui。"""
    return [Path(name).resolve() for name in opt.roots or [ROOT / n for n in DEFAULT]]


def parse(argv: list[str]):
    """参数面：对侧目录与若干扫描根。"""
    ap = cli.parser(
        "ci/checks/tui_isolation.py",
        "用法: ci/checks/tui_isolation.py [--agentrt DIR] [根...]",
        "双通路依赖隔离：TUI 不链 agentrt，agentrt 不依赖 TUI。")
    ap.add_argument("--agentrt", metavar="DIR")
    ap.add_argument("roots", nargs="*")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    opt = parse(argv)
    try:
        return judge(roots_of(opt), peer_dir(opt))
    except tomllib.TOMLDecodeError as exc:
        print(f"tui-isolation: 清单不可解析: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
