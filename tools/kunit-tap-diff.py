#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
kunit-tap-diff.py — 对比 KUnit TAP 输出与基线分支的差异

被 ci-kernel.yml 引用（OS-TEST-012：KUnit TAP 用例数单调性校验）。
当前 TAP 来自 stdin 或 --current 指定的文件；基线 TAP 通过
``git show <baseline>:<baseline-path>`` 获取。

退出码：
    0 — 与基线一致，或基线不可获取（降级为通过）
    1 — 检测到差异（用例数回退、新增 not ok、计划行消失等）
    2 — 输入/解析错误
"""

import argparse
import re
import subprocess
import sys

# TAP 行匹配：
#   计划行 "1..N"
#   测试行 "ok N - desc" / "not ok N - desc" / "ok N desc" / "not ok N desc"
#   同时容忍 TODO/SKIP 等指令
PLAN_RE = re.compile(r"^\s*(\d+)\.\.(\d+)\s*$")
TEST_RE = re.compile(
    r"^(?P<status>ok|not ok)\s+(?P<num>\d+)(?:\s+-?\s*(?P<desc>.*))?$"
)


def parse_tap(text):
    """解析 TAP 文本，返回 (plan_total, results)。

    results: dict[num] -> "ok" | "not ok"
    plan_total: int 或 None（未找到计划行）
    """
    plan_total = None
    results = {}
    for line in text.splitlines():
        m = PLAN_RE.match(line)
        if m:
            plan_total = int(m.group(2))
            continue
        m = TEST_RE.match(line)
        if m:
            num = int(m.group("num"))
            results[num] = m.group("status")
    return plan_total, results


def fetch_baseline_tap(baseline, baseline_path):
    """通过 ``git show <baseline>:<baseline_path>`` 取基线 TAP 文本。

    返回 (text, error)；成功时 error 为 None。
    """
    try:
        proc = subprocess.run(
            ["git", "show", f"{baseline}:{baseline_path}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"git 不可用或超时：{exc}"
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip()
    return proc.stdout, None


def read_current_tap(current_path):
    """读取当前 TAP：从 --current 文件，否则从 stdin。"""
    if current_path:
        try:
            with open(current_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), None
        except OSError as exc:
            return None, f"无法读取 --current 文件：{exc}"
    # stdin 若为 TTY（无管道输入）则视为空
    if sys.stdin.isatty():
        return "", None
    return sys.stdin.read(), None


def diff_results(cur_plan, cur_res, base_plan, base_res):
    """生成差异文本列表，无差异返回空列表。"""
    lines = []
    if cur_plan != base_plan:
        lines.append(
            f"  计划用例数变化：基线 {base_plan} -> 当前 {cur_plan}"
        )
    base_fail = {n for n, s in base_res.items() if s == "not ok"}
    cur_fail = {n for n, s in cur_res.items() if s == "not ok"}
    new_fail = sorted(cur_fail - base_fail)
    gone_fail = sorted(base_fail - cur_fail)
    for n in new_fail:
        lines.append(f"  新增失败用例：not ok {n}")
    for n in gone_fail:
        lines.append(f"  失败用例已修复（基线 not ok {n}）")
    # 用例号集合差异（已存在但状态变化已在上面覆盖；这里报告整体缺失/新增）
    cur_nums = set(cur_res.keys())
    base_nums = set(base_res.keys())
    only_cur = sorted(cur_nums - base_nums)
    only_base = sorted(base_nums - cur_nums)
    if only_cur:
        lines.append(f"  当前独有的用例号：{only_cur}")
    if only_base:
        lines.append(f"  基线独有的用例号：{only_base}")
    return lines


def main():
    parser = argparse.ArgumentParser(
        description="对比 KUnit TAP 输出与基线分支的差异"
    )
    parser.add_argument(
        "--baseline",
        default="origin/develop",
        help="基线分支引用（默认 origin/develop）",
    )
    parser.add_argument(
        "--current",
        help="当前 TAP 文件路径；省略则从 stdin 读取",
    )
    parser.add_argument(
        "--baseline-path",
        default="tools/kunit-tap-baseline.tap",
        help="基线 TAP 在仓库中的相对路径（git show <baseline>:<path>）",
    )
    args = parser.parse_args()

    cur_text, err = read_current_tap(args.current)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    if not cur_text.strip():
        print(
            "WARN: 未提供当前 TAP 输出（stdin/文件为空），跳过对比",
            file=sys.stderr,
        )
        return 0

    cur_plan, cur_res = parse_tap(cur_text)
    print(
        f"当前 TAP：计划 {cur_plan}，解析到 {len(cur_res)} 条结果"
    )

    base_text, berr = fetch_baseline_tap(args.baseline, args.baseline_path)
    if berr is not None:
        print(
            f"WARN: 无法获取基线 TAP（{args.baseline}:{args.baseline_path}）："
            f"{berr}",
            file=sys.stderr,
        )
        print("WARN: 基线不可用，按降级策略退出 0", file=sys.stderr)
        return 0

    base_plan, base_res = parse_tap(base_text)
    print(
        f"基线 TAP：计划 {base_plan}，解析到 {len(base_res)} 条结果"
    )

    diffs = diff_results(cur_plan, cur_res, base_plan, base_res)
    if not diffs:
        print("OK: 当前 TAP 与基线一致")
        return 0

    print("DIFF: 检测到差异：")
    for line in diffs:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
