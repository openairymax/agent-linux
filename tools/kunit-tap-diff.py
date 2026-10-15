#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
kunit-tap-diff.py — KUnit KTAP 用例数单调性门禁（OS-TEST-012）

解析 ``kunit.py run --raw_output=kunit`` 产出的嵌套 KTAP，与仓库内基线快照
（默认 ``tools/kunit-tap-baseline.tap``）对比。基线是一份「棘轮」：用例总数
只允许增长，套件不得被静默删除。确需下调基线时，必须在同一次变更中显式修改
基线文件，使删除动作在评审 diff 中可见。

基线写入时做规范化：只保留子测试头与结果行，丢弃内核噪声（栈回溯、bench
计时、版本串等），因此重复执行 ``--update`` 对同一批用例产出逐字节一致的
文件，评审 diff 只反映真实的用例增减。

KTAP 为嵌套格式：每个子测试以 ``# Subtest: <名>`` 起头，其叶子用例缩进一级，
并由父级追加一行聚合结果。据此：

    叶子用例数 = 结果行总数 - ``# Subtest:`` 个数

门禁失败条件（任一命中即退出码 1）：
  * 叶子用例总数回退：current < baseline；
  * 基线中的套件在 current 中缺失；
  * current 出现基线中不存在的 not ok 用例。

退出码：
    0 — 通过（用例数持平或增长）
    1 — 门禁失败
    2 — 输入/解析/使用错误（含 current 为空、基线缺失或不可解析）
"""

import argparse
import re
import sys

_SUBTEST_RE = re.compile(r"^\s*#\s*Subtest:\s*(?P<name>\S(?:.*\S)?)\s*$")
_RESULT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<status>ok|not ok)\s+(?P<num>\d+)"
    r"(?:\s+(?P<desc>.*?))?\s*$"
)


def _describe(desc):
    """归一化 TAP 结果描述：剥离前导 "- " 与首尾空白。"""
    if not desc:
        return ""
    return desc.strip().lstrip("-").strip()


def parse_ktap(text):
    """解析嵌套 KTAP，返回统计 dict；无法构成有效 KTAP 时返回 None。

    dict 字段：
        leaf_total : int        叶子用例数
        suites     : list[str]  按出现顺序的套件（子测试）名
        failures   : set[str]   状态为 not ok 的用例/套件描述名
    """
    suites = []
    results = []
    for line in text.splitlines():
        subtest = _SUBTEST_RE.match(line)
        if subtest:
            suites.append(subtest.group("name"))
            continue
        result = _RESULT_RE.match(line)
        if result:
            results.append(
                (result.group("status"), _describe(result.group("desc")))
            )
    leaf_total = len(results) - len(suites)
    if not suites or not results or leaf_total < 0:
        return None
    return {
        "leaf_total": leaf_total,
        "suites": suites,
        "failures": {name for status, name in results if status == "not ok"},
    }


def compare(current, baseline):
    """比对 current 与 baseline，返回 (passed, messages)。"""
    messages = []
    failed = False

    if current["leaf_total"] < baseline["leaf_total"]:
        failed = True
        messages.append(
            f"FAIL 叶子用例数回退：基线 {baseline['leaf_total']}"
            f" -> 当前 {current['leaf_total']}"
        )
    elif current["leaf_total"] > baseline["leaf_total"]:
        messages.append(
            f"OK   叶子用例数增长：基线 {baseline['leaf_total']}"
            f" -> 当前 {current['leaf_total']}"
        )
    else:
        messages.append(f"OK   叶子用例数持平：{current['leaf_total']}")

    present = set(current["suites"])
    for name in baseline["suites"]:
        if name not in present:
            failed = True
            messages.append(f"FAIL 套件缺失：{name}")

    for name in sorted(current["failures"] - baseline["failures"]):
        failed = True
        messages.append(f"FAIL 新增失败用例：{name}")

    return not failed, messages


def _read_file(path, label):
    """读取文本文件，返回 (text, error)；成功时 error 为 None。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), None
    except OSError as exc:
        return None, f"{label}读取失败 {path!r}：{exc}"


def _read_current(path):
    """读取当前 KTAP：优先 --current 文件，否则读 stdin。"""
    if path:
        return _read_file(path, "当前 TAP ")
    if sys.stdin.isatty():
        return "", None
    return sys.stdin.read(), None


def _normalize(text):
    """抽取 KTAP 语义行（子测试头与结果行），丢弃内核噪声行。"""
    kept = [
        line.rstrip()
        for line in text.splitlines()
        if _SUBTEST_RE.match(line) or _RESULT_RE.match(line)
    ]
    return "\n".join(kept) + "\n"


def _write_baseline(path, text):
    """将规范化后的 KTAP 写入基线快照，返回 error 或 None。"""
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_normalize(text))
    except OSError as exc:
        return f"写入基线失败 {path!r}：{exc}"
    return None


def main():
    parser = argparse.ArgumentParser(
        description="KUnit KTAP 用例数单调性门禁（OS-TEST-012）"
    )
    parser.add_argument("--current", help="当前 KTAP 文件；省略则读 stdin")
    parser.add_argument(
        "--baseline-file",
        default="tools/kunit-tap-baseline.tap",
        help="基线快照路径（默认 tools/kunit-tap-baseline.tap）",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="用当前 KTAP 规范化后覆盖基线快照并退出（显式下调棘轮）",
    )
    args = parser.parse_args()

    text, err = _read_current(args.current)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    if not text.strip():
        print(
            "ERROR: 当前 KTAP 为空（kunit 未运行或构建失败）",
            file=sys.stderr,
        )
        return 2

    current = parse_ktap(text)
    if current is None:
        print("ERROR: 当前输入不是可解析的 KTAP", file=sys.stderr)
        return 2

    if args.update:
        err = _write_baseline(args.baseline_file, text)
        if err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 2
        print(
            f"OK   已更新基线 {args.baseline_file}："
            f"套件 {len(current['suites'])}，叶子用例 {current['leaf_total']}"
        )
        return 0

    base_text, err = _read_file(args.baseline_file, "基线 ")
    if err:
        print(f"ERROR: {err}（可用 --update 生成基线）", file=sys.stderr)
        return 2
    baseline = parse_ktap(base_text)
    if baseline is None:
        print(
            f"ERROR: 基线 {args.baseline_file} 不是可解析的 KTAP",
            file=sys.stderr,
        )
        return 2

    print(
        f"当前：套件 {len(current['suites'])}，叶子用例 {current['leaf_total']}；"
        f"基线：套件 {len(baseline['suites'])}，叶子用例 {baseline['leaf_total']}"
    )
    passed, messages = compare(current, baseline)
    for message in messages:
        print(message)
    if not passed:
        print("FAIL: KTAP 单调性门禁未通过（OS-TEST-012）")
        return 1
    print("OK: KTAP 单调性门禁通过（OS-TEST-012）")
    return 0


if __name__ == "__main__":
    sys.exit(main())