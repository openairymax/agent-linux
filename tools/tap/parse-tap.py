#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
parse-tap.py — 解析 TAP（Test Anything Protocol）输出

由 ci/gates/kernel-kselftest.sh 调用（OS-TEST-013~022：kselftest TAP 解析）。
读取 ``--input`` 指定的 TAP 文件，统计 ok / not ok 用例数；
当 ``--fail-on-not-ok`` 且存在 not ok 行时以退出码 1 终止。

退出码：
    0 — 解析成功且（未指定 --fail-on-not-ok，或无 not ok）
    1 — 指定了 --fail-on-not-ok 且存在 not ok
    2 — 输入/解析错误
"""

import argparse
import re
import sys

# TAP 14 兼容：计划行 "1..N"，测试行 "ok N - desc" / "not ok N - desc"
PLAN_RE = re.compile(r"^\s*(\d+)\.\.(\d+)\s*(#.*)?$")
TEST_RE = re.compile(
    r"^(?P<status>ok|not ok)\s+(?P<num>\d+)"
    r"(?:\s+(?P<rest>.*))?$"
)


def _plan_total(line):
    """计划行 → 总用例数；非计划行返回 None。"""
    m = PLAN_RE.match(line)
    if not m:
        return None
    return int(m.group(2))


def _test_result(line):
    """测试行 → (num, status, desc)；非测试行返回 None。"""
    m = TEST_RE.match(line)
    if not m:
        return None
    status = m.group("status")
    rest = m.group("rest") or ""
    # 剥离前导 "- "，便于展示描述
    desc = rest.lstrip("- ").strip()
    return int(m.group("num")), status, desc


def parse_tap(text):
    """解析 TAP 文本。

    返回 dict：
        plan_total : int 或 None
        ok_count   : int
        not_ok_count : int
        results     : list[(num, status, desc)]
    """
    plan_total = None
    results = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        total = _plan_total(line)
        if total is not None:
            plan_total = total
            continue
        item = _test_result(line)
        if item is None:
            continue
        results.append(item)
    ok_count, not_ok_count = _counts(results)
    return {
        "plan_total": plan_total,
        "ok_count": ok_count,
        "not_ok_count": not_ok_count,
        "results": results,
    }


def _counts(results):
    """(ok 数, not ok 数)：status 只取 ok / not ok 两种取值。"""
    ok = sum(status == "ok" for _, status, _ in results)
    return ok, len(results) - ok


def _print_failures(results):
    """逐条打印未通过用例。"""
    for num, status, desc in results:
        if status == "not ok":
            print(f"  not ok {num} - {desc}")


def main():
    parser = argparse.ArgumentParser(
        description="解析 TAP 输出并可选地在出现 not ok 时失败"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="TAP 文件路径（必填）",
    )
    parser.add_argument(
        "--fail-on-not-ok",
        action="store_true",
        help="当存在 not ok 行时以退出码 1 退出",
    )
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        print(f"ERROR: 无法读取 --input 文件 {args.input!r}：{exc}",
              file=sys.stderr)
        return 2

    summary = parse_tap(text)
    plan = summary["plan_total"]
    ok = summary["ok_count"]
    not_ok = summary["not_ok_count"]

    print(f"TAP plan: {plan}")
    print(f"ok:     {ok}")
    print(f"not ok: {not_ok}")

    if not_ok > 0:
        print("失败用例：")
        _print_failures(summary["results"])
        if args.fail_on_not_ok:
            print(f"FAIL: {not_ok} 个 not ok 用例（--fail-on-not-ok 已启用）")
            return 1
        print("WARN: 存在 not ok 用例，但未启用 --fail-on-not-ok")
    else:
        print("OK: 全部用例通过")

    return 0


if __name__ == "__main__":
    sys.exit(main())
