#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""ci/check-workflows.py —— 守住 workflow 薄化约束

三条规则：

1. 结构上限：每个 workflow 的 job 数 ≤ 预算，内联 ``run: |`` 块数 ≤ 预算
   （预算见 ci/workflow-budgets.txt，只许下调）。
2. 棘轮：内联块数不得高于 git HEAD 中的同名文件——调低预算才算迁移，
   把逻辑抄回 YAML 会让这条规则变红。
3. 已迁移文件（内联预算为 0）不得出现被 gate 收编的模式：宿主清理、
   docker load、摘要校验、qemu 安装、串口 TAP 解析。

为什么需要它：这些约束此前只存在于文档里，没有执行者。78 段内联 shell
就是这样一段段长出来的——每一次单看都合理，合起来就没有人能在本地验证。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
BUDGETS = ROOT / "ci" / "workflow-budgets.txt"

# 内联预算为 0 的文件里不允许再出现的模式：(正则, 应改用的 gate)
FORBIDDEN = [
    (re.compile(r"/usr/share/dotnet"), "ci/lib/host.sh free_disk"),
    (re.compile(r"docker\s+load"), "ci/ci.sh container-load"),
    (re.compile(r"sha256sum\s+-c"), "ci/ci.sh iso-checksum | image-verify"),
    (re.compile(r"apt-get\s+install\s+-y.*qemu"), "ci/ci.sh qemu-setup"),
    (re.compile(r"AIRYMAX-TAP\|"), "ci/ci.sh tap-extract"),
]

RUN_BLOCK = re.compile(r"^(\s*)run:\s*[|>]")


def count_inline(text: str) -> int:
    """统计 ``run: |`` / ``run: >`` 块数（单行 ``run: cmd`` 不计）。"""
    lines = text.splitlines()
    n = 0
    i = 0
    while i < len(lines):
        m = RUN_BLOCK.match(lines[i])
        if not m:
            i += 1
            continue
        n += 1
        indent = len(m.group(1))
        i += 1
        while i < len(lines):
            s = lines[i]
            if s.strip() and len(s) - len(s.lstrip()) <= indent:
                break
            i += 1
    return n


def count_jobs(text: str) -> int:
    """统计 ``jobs:`` 下的一级 job 名。"""
    lines = text.splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.rstrip() == "jobs:")
    except StopIteration:
        return 0
    n = 0
    for s in lines[start + 1:]:
        if not s.strip():
            continue
        if not s.startswith(" "):
            break
        if re.match(r"^  [A-Za-z0-9_-]+:\s*$", s):
            n += 1
    return n


def load_budgets() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for raw in BUDGETS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SystemExit(f"ci/workflow-budgets.txt 格式错误: {raw!r}")
        name, inline, jobs = parts
        out[name] = (int(inline), int(jobs))
    return out


def head_inline(name: str) -> int | None:
    """git HEAD 中同一文件的内联块数；不在版本控制里则返回 None。"""
    try:
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:.github/workflows/{name}"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return count_inline(blob)


def main() -> int:
    budgets = load_budgets()
    files = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
    errors: list[str] = []

    for name in files:
        if name not in budgets:
            errors.append(f"{name}: 未登记预算（请写入 ci/workflow-budgets.txt）")
    for name in budgets:
        if name not in files:
            errors.append(f"{name}: 预算已登记但文件不存在")

    total = 0
    for name in files:
        text = (WORKFLOWS / name).read_text()
        inline = count_inline(text)
        jobs = count_jobs(text)
        total += inline
        if name not in budgets:
            continue
        max_inline, max_jobs = budgets[name]

        if inline > max_inline:
            errors.append(f"{name}: 内联 run 块 {inline} > 预算 {max_inline}")
        if jobs > max_jobs:
            errors.append(f"{name}: job 数 {jobs} > 预算 {max_jobs}")
        if jobs == 0:
            errors.append(f"{name}: 没有解析到任何 job")

        base = head_inline(name)
        if base is not None and inline > base and inline > max_inline:
            errors.append(f"{name}: 内联块由 HEAD 的 {base} 增至 {inline}（棘轮回退）")

        if max_inline == 0:
            for pat, gate in FORBIDDEN:
                for no, s in enumerate(text.splitlines(), 1):
                    if pat.search(s):
                        errors.append(
                            f"{name}:{no}: 已收编逻辑回流（{pat.pattern}），应走 {gate}")

    if errors:
        print("workflow 薄化检查未通过:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"workflow 薄化检查通过: {len(files)} 个文件, 内联 run 块合计 {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
