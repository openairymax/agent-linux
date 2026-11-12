#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""accel_contract.py — 加速后端探测的三份输出契约

门禁钉的是「输出长什么样」：行式的四段、JSON 的字段与证据前缀、以及
两种渲染必须同源。三份判据此前各自内嵌在 gate 的 heredoc 里，读一份
契约要在 shell 与 Python 之间来回切换，也无法单独喂用例；做成模块后
每份契约一个入口，失败文案不变。

约定：
  * 后端顺序按 cuda、rocm、vulkan、cpu 固定，顺序即契约的一部分：
    外部解析按下标取段，顺序漂移与字段漂移一样会让它静默读错。
  * 字段集合按字典序比对而不是「包含」——多一个字段就是契约漂移。
  * 证据前缀只认固定集合；带冒号才叫「有前缀」，故 builtin 之外还要求
    冒号在项内，避免把 "ok" 这类裸词当成前缀成立的证据。
  * 行式与 JSON 的段必须逐字相同：两者分开维护，就会出现监控看到
    vulkan=present、脚本读到 present=false 的自相矛盾。

用法: accel_contract.py line  RAW
      accel_contract.py json  RAW
      accel_contract.py agree LINE RAW

退出码: 0 契约成立；1 契约被违反（消息写 stderr）；2 用法错误。
"""

import json
import sys

WANT = ["cuda", "rocm", "vulkan", "cpu"]
FLAGS = ("present", "absent")
ROW_FIELDS = ["backend", "evidence", "present"]
PREFIX = {"ok", "missing", "denied", "empty", "stale", "unverified",
          "builtin"}


def _fail(msg):
    sys.exit(msg)


def _pairs(raw):
    got = raw.split()
    if len(got) != len(WANT):
        _fail("段数 %d，应为 %d: %r" % (len(got), len(WANT), raw))
    return got, [item.split("=", 1) for item in got]


def check_line(raw):
    """行式：四段、顺序、状态取值与 CPU 恒在。"""
    got, pairs = _pairs(raw)
    if [pair[0] for pair in pairs] != WANT:
        _fail("后端顺序偏离契约: %r" % raw)
    for pair in pairs:
        flag = pair[1] if len(pair) == 2 else ""
        if flag not in FLAGS:
            _fail("状态 %r 不是 present/absent" % flag)
    if got[-1] != "cpu=present":
        _fail("CPU 后端必须恒为 present")
    return 0


def _item(name, item):
    if not isinstance(item, str):
        _fail("%s: 证据必须是字符串" % name)
    prefix = item.split(":", 1)[0]
    if prefix not in PREFIX:
        _fail("%s: 证据前缀未知: %r" % (name, item))
    if prefix != "builtin" and ":" not in item:
        _fail("%s: 证据缺少前缀: %r" % (name, item))


def _row(row):
    name = row.get("backend")
    if sorted(row) != ROW_FIELDS:
        _fail("%s: 元素字段 %r 偏离契约" % (name, sorted(row)))
    if not isinstance(row["present"], bool):
        _fail("%s: present 必须是布尔值" % name)
    evidence = row["evidence"]
    if not isinstance(evidence, list) or not evidence:
        _fail("%s: 证据不得为空" % name)
    for item in evidence:
        _item(name, item)


def check_json(raw):
    """JSON：顶层只有 backends，逐行字段与证据形状同契约。"""
    obj = json.loads(raw)
    if sorted(obj) != ["backends"]:
        _fail("顶层字段 %r，应为 ['backends']" % sorted(obj))
    rows = obj["backends"]
    if [row.get("backend") for row in rows] != WANT:
        _fail("后端顺序偏离契约")
    for row in rows:
        _row(row)
    return 0


def check_agree(line, raw):
    """行式与 JSON 同源：行式每一段都是 JSON 同一行的投影。"""
    rows = json.loads(raw)["backends"]
    want = " ".join(
        "%s=%s" % (row["backend"],
                   "present" if row["present"] else "absent")
        for row in rows)
    if line != want:
        _fail("两种输出不一致: %r != %r" % (line, want))
    return 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "line" and len(argv) == 3:
        return check_line(argv[2])
    if mode == "json" and len(argv) == 3:
        return check_json(argv[2])
    if mode == "agree" and len(argv) == 4:
        return check_agree(argv[2], argv[3])
    print("用法: accel_contract.py (line|json|agree) ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
