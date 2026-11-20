#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/wp_scope.py — 提交范围与施工图纸产出物的对账（§2.1／§4.1）
#
# 本版把改动限定在施工图纸 §6.0 的卡片里：每个任务声明自己会碰哪些路径，
# 表外路径不属本版。判据只有一条，但少了它，任何一次「顺手改一下」都能把
# 范围悄悄撑开，而评审只看到提交标题。
#
# 判定两半：
#
#   归属  从提交信息取 WP 号（`WP: Q-04` 行优先，其次正文里出现在册内的
#         任务号），按号在 §6.0 查到产出物清单。取不到号的提交记为未归属。
#   范围  提交改动的每个路径必须落在某个产出物之内。产出物支持三种写法：
#         目录（尾斜杠，前缀匹配）、通配（`*`）、以及 `{a,b}` 展开；子仓库
#         指针（`system`／`distro`／`kernel`）按产出物的首段放行，因为指针
#         是子仓改动在主仓里的唯一形态，逐文件对账发生在子仓自己的提交上。
#
# 路径前缀只认根表：kernel/、system/、distro/、ci/、.github/，文档树两项
# 10-references/、20-archive/ 不在本仓比对。表外前缀一律判负——产出物写错
# 一行，等于给越界改动开了长期通行证。
#
# 接线面（ci/、.github/）整片放行：门禁入口、回归、公共库与流水线不属于
# 任何单张卡，但每张卡落地时都要碰。产品树仍逐卡对账，越界照判。
#
# 施工图纸在 docs-closed，单仓检出看不到：找不到册子时本门禁退 3（不适用），
# 不冒充通过。
#
# 未归属不判负：册子之外的提交（文档、清理、子仓指针）本就常见，拦下它们
# 只会逼人往提交信息里塞一个不相干的号。留痕在报告里，谁都能看见。
#
# 用法: python3 ci/checks/wp_scope.py [--plan FILE] [--range A..B] [--root DIR]
# 退出码: 0 全部归属且无越界；1 有越界；3 册子或提交缺席；5 参数或仓库不满足。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================
from __future__ import annotations

import fnmatch
import itertools
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import cli

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = (
    "docs/docs-closed/agent-linux-dev/0.0.5-极境OS任务方案.md",
    "../docs/docs-closed/agent-linux-dev/0.0.5-极境OS任务方案.md",
)
ROOTS = ("kernel/", "system/", "distro/", "ci/", ".github/")
DOCS = ("10-references/", "20-archive/")
SHARED = ("ci/", ".github/")
ID = re.compile(r"^[A-Z]+-\d+(?:-\d+)?$")
TAG = re.compile(r"WP[:：]\s*([A-Z]+-\d+(?:-\d+)?)")
TOKEN = re.compile(r"\b([A-Z]{1,3}-\d{1,3}(?:-\d{1,3})?)\b")
GROUP = re.compile(r"\{([^{}]*)\}")


def expand(pat: str) -> list[str]:
    """花括号展开：逐组按逗号分列后取笛卡尔积。"""
    parts = GROUP.split(pat)
    choices = [item.split(",") if idx % 2 else [item] for idx, item in enumerate(parts)]
    return ["".join(combo) for combo in itertools.product(*choices)]


def arts(field: str) -> list[str]:
    """产出物列：去括注、按顿号切分、花括号展开。"""
    clean = re.sub(r"（[^）]*）", "", field)
    return [pat for item in clean.split("、") if item.strip()
            for pat in expand(item.strip())]


def capsule(cols: list[str]) -> bool:
    """表行是否为一张卡：列数够、任务号合法、产出物非空。"""
    return len(cols) >= 10 and ID.match(cols[1]) is not None and bool(cols[3])


def rows(path: Path):
    """施工图纸里可解析的卡片列。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = [col.strip() for col in line.split("|")]
        if capsule(cols):
            yield cols


def stem(task: str) -> str:
    """任务号去掉末段即 WP 号。"""
    return "-".join(task.split("-")[:2])


def merge(wps: dict[str, list[str]], key: str, pats: list[str]) -> None:
    """WP 名下的产出物并集，保序去重。"""
    bucket = wps.setdefault(key, [])
    bucket += [pat for pat in pats if pat not in bucket]


def read_plan(path: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """解析两级账目：单卡产出物，与 WP 号名下的并集。

    §6.1–6.8 的每张卡给一条「任务 → 产出物」，§6.0 的 WP 号是任务号去掉
    末段。提交按 WP 归属，因此 WP 的产出物是它名下全部卡片的并集；按任务号
    归属时只取那一张卡。两级都收，提交里写哪一级都认。
    """
    cards: dict[str, list[str]] = {}
    for cols in rows(path):
        cards.setdefault(cols[1], arts(cols[3]))
    wps: dict[str, list[str]] = {}
    for task, pats in cards.items():
        merge(wps, stem(task), pats)
    return cards, wps


def git(root: Path, *args: str) -> str | None:
    """git 调用的统一包装；失败返回 None，由调用点决定退 3 还是 5。"""
    try:
        done = subprocess.run(("git", "-C", str(root)) + args, check=True,
                              capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout


def records(text: str) -> list[tuple[str, str]]:
    """log 输出切成 (sha, 信息)；`%x1f` 分字段，`%x1e` 分记录。"""
    return [(rec.partition("\x1f")[0].strip(), rec.partition("\x1f")[2])
            for rec in text.split("\x1e") if rec.strip()]


def commits(root: Path, rng: str) -> list[tuple[str, str]] | None:
    """范围内的提交 (sha, 信息)；范围不可读返回 None。"""
    text = git(root, "log", "--format=%H%x1f%B%x1e", rng)
    return None if text is None else records(text)


def touched(root: Path, sha: str) -> list[str]:
    """单个提交改动的路径。"""
    text = git(root, "show", "--pretty=format:", "--name-only", "--no-renames", sha)
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def pick(msg: str, ids: dict[str, list[str]]) -> str | None:
    """提交信息里第一个登记过的任务号；`WP:` 行优先。"""
    tag = TAG.search(msg)
    order = [tag.group(1)] if tag else []
    return next((tok for tok in order + TOKEN.findall(msg) if tok in ids), None)


def hit(path: str, pat: str) -> bool:
    """单个路径是否落在产出物内：目录前缀、通配、或子仓指针首段。"""
    if pat.endswith("/"):
        return path.startswith(pat)
    return fnmatch.fnmatch(path, pat) or path == pat.split("/")[0]


def outside(pat: str) -> bool:
    """产出物自身的前缀是否越出根表。"""
    return not pat.startswith(ROOTS + DOCS)


def shared(path: str) -> bool:
    """接线面：门禁入口、回归、公共库与流水线。"""
    return path.startswith(SHARED)


def covered(path: str, pats: list[str]) -> bool:
    """路径是否落在任一产出物内（接线面另行放行）。"""
    return shared(path) or any(hit(path, pat) for pat in pats)


def judge(root: Path, sha: str, wid: str, pats: list[str]) -> list[str]:
    """单个提交的越界明细。"""
    bad = [f"{sha[:7]} {wid}: 产出物前缀越界 {pat}" for pat in pats if outside(pat)]
    changed = touched(root, sha)
    bad += [f"{sha[:7]} {wid}: {path} 不在产出物内" for path in changed
            if not covered(path, pats)]
    print(f"wp-scope: {sha[:7]} {wid}: {len(changed)} 个路径，{len(pats)} 项产出物")
    return bad


def audit(root: Path, table, recs: list[tuple[str, str]]) -> tuple[list[str], int]:
    """逐提交对账；返回 (越界明细, 未归属数)。"""
    cards, wps = table
    ids = {**wps, **cards}
    bad: list[str] = []
    loose = 0
    for sha, msg in recs:
        wid = pick(msg, ids)
        if wid is None:
            loose += 1
            print(f"wp-scope: {sha[:7]} 未归属: {msg.splitlines()[0][:60]}")
            continue
        bad += judge(root, sha, wid, cards.get(wid) or wps[wid])
    return bad, loose


def discover(root: Path) -> Path | None:
    """按候选位置找施工图纸；单仓检出时返回 None。"""
    for name in CANDIDATES:
        if (root / name).is_file():
            return root / name
    return None


def fail(msg: str, code: int) -> int:
    """打印原因并回传退出码。"""
    print(msg, file=sys.stderr)
    return code


def plan_of(opt) -> tuple[tuple[dict, dict] | None, int]:
    """读施工图纸；返回 (账目, 退出码)，册子缺席或不可解析时账目为 None。"""
    plan = opt.plan or discover(opt.root)
    if plan is None:
        return None, fail("wp-scope: 未检出施工图纸，不适用", 3)
    if not plan.is_file():
        return None, fail(f"施工图纸不存在: {plan}", 5)
    table = read_plan(plan)
    if not table[0]:
        return None, fail(f"施工图纸没有可解析的任务行: {plan}", 5)
    return table, 0


def recs_of(opt) -> tuple[list[tuple[str, str]] | None, int]:
    """取提交范围；返回 (提交清单, 退出码)，范围不可读或为空时清单为 None。"""
    recs = commits(opt.root, opt.rng)
    if recs is None:
        return None, fail(f"提交范围不可读: {opt.rng}", 5)
    if not recs:
        return None, fail("wp-scope: 范围内无提交，不适用", 3)
    return recs, 0


def parse(argv: list[str]):
    """参数面：册子、范围与仓库根。"""
    ap = cli.parser(
        "ci/checks/wp_scope.py",
        "用法: ci/checks/wp_scope.py [--plan FILE] [--range A..B] [--root DIR]",
        "提交范围与施工图纸产出物的对账。")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--range", dest="rng", default="HEAD~1..HEAD")
    ap.add_argument("--root", type=Path, default=ROOT)
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    opt = parse(argv)
    table, code = plan_of(opt)
    if table is None:
        return code
    recs, code = recs_of(opt)
    if recs is None:
        return code
    bad, loose = audit(opt.root, table, recs)
    for line in bad:
        print(f"wp-scope: {line}", file=sys.stderr)
    print(f"wp-scope: {len(recs)} 个提交，{len(table[0])} 张卡，{len(table[1])} 个 WP，"
          f"{loose} 个未归属，{len(bad)} 处越界")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
