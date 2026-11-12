#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""llm_contract.py — 离线自检的三份对外契约比对

自检门禁钉住的不是「模型答得对不对」，而是「判定长什么样」：JSON 判定的
字段集合、逐项顺序与失败归因链，行式输出的逐项同形，状态文件的落盘内容。
这三处此前各自内嵌在 gate 的 heredoc 里，同一份契约读起来要跨三个 shell
函数切换上下文；做成模块后每份契约一个入口，判据分段，失败文案不变。

约定：
  * 字段集合按字典序比对而不是「包含」——多一个字段就是契约漂移，
    后续读者按旧契约解析会读到不存在的东西。
  * 失败时要求首个失败项即归因项，其后每一项记成被它阻塞并沿用同一
    归因：从输出里消失的项与通过的项，在运维眼里没有区别。
  * 判定必须落盘（末项 status 通过），没落盘的判定等于没判定。

用法: llm_contract.py json   NAMES RAW STATE REASON FAIL [DIGEST]
      llm_contract.py lines  NAMES OUT
      llm_contract.py status HOME RAW

退出码: 0 契约成立；1 契约被违反（消息写 stderr）；2 用法错误。
"""

import json
import sys

# 顶层与逐项字段集合，顺序即契约；多字段少字段都算漂移。
TOP_FIELDS = ["backend", "checks", "digest", "reason", "state"]
ROW_FIELDS = ["detail", "name", "ok", "reason"]
STATE_FIELDS = ("state", "backend", "reason", "digest")
BLOCK = "blocked by "
LAST = "status"


def _shape(obj, names):
    """顶层字段、逐项顺序与逐项形状；任一不符即停在这里。"""
    if sorted(obj) != TOP_FIELDS:
        sys.exit("字段集合 %r 偏离契约" % sorted(obj))
    rows = obj["checks"]
    got = [row.get("name") for row in rows]
    if got != names:
        sys.exit("检查项顺序 %r 偏离契约" % got)
    for row in rows:
        if sorted(row) != ROW_FIELDS:
            sys.exit("%s: 元素字段 %r 偏离契约"
                     % (row.get("name"), sorted(row)))
        if not isinstance(row["ok"], bool):
            sys.exit("%s: ok 必须是布尔值" % row["name"])
    return rows


def _state(obj, state, reason, digest):
    """state/reason 是运维唯一会照着做的字段；摘要只在期望值非空时比。"""
    if (obj["state"], obj["reason"]) != (state, reason or None):
        sys.exit("state/reason %r，应为 %r"
                 % ((obj["state"], obj["reason"]), (state, reason or None)))
    if digest and obj["digest"] != digest:
        sys.exit("清单摘要 %r，应为 %r" % (obj["digest"], digest))


def _passed(rows):
    bad = [row["name"] for row in rows if not row["ok"]]
    if bad:
        sys.exit("不应有失败项: %r" % bad)


def _blocked(rows, names, want):
    """首个失败项必须是归因项，其后各项记被它阻塞并沿用同一归因。"""
    bad = [row["name"] for row in rows if not row["ok"]]
    if not bad or bad[0] != want:
        sys.exit("首个失败项 %r，应为 %r" % (bad[:1], want))
    at = names.index(want)
    cause = rows[at]
    for row in rows[at + 1:-1]:
        if row["ok"] or not row["detail"].startswith(BLOCK):
            sys.exit("%s: 应记为被阻塞" % row["name"])
        if row["reason"] != cause["reason"]:
            sys.exit("%s: 阻塞归因 %r 与失败项不符"
                     % (row["name"], row["reason"]))


def _sealed(rows):
    if not rows[-1]["ok"] or rows[-1]["name"] != LAST:
        sys.exit("失败判定未落盘")


def check_json(argv):
    names = argv[0].split()
    raw, state, reason, want = argv[1:5]
    digest = argv[5] if len(argv) > 5 else ""
    obj = json.loads(raw)
    rows = _shape(obj, names)
    _state(obj, state, reason, digest)
    if not want:
        _passed(rows)
        return 0
    _blocked(rows, names, want)
    _sealed(rows)
    return 0


def check_lines(argv):
    names = argv[0].split()
    rows = argv[1].strip().splitlines()
    if len(rows) != len(names) + 1:
        sys.exit("行数 %d，应为 %d" % (len(rows), len(names) + 1))
    for name, row in zip(names, rows):
        if not row.startswith("%s=" % name):
            sys.exit("第 %s 行偏离契约: %r" % (name, row))
        if row != "%s=ok" % name and not row.startswith("%s=ok " % name):
            sys.exit("%s 未通过: %r" % (name, row))
    if not rows[-1].startswith("state="):
        sys.exit("末行不是状态行: %r" % rows[-1])
    return 0


def check_status(argv):
    home, raw = argv
    obj = json.loads(raw)
    want = {key: obj[key] for key in STATE_FIELDS}
    with open("%s/status.json" % home, encoding="utf-8") as handle:
        kept = json.load(handle)
    extra = sorted(set(kept) - {"schema"} - set(want))
    if extra:
        sys.exit("状态文件含契约外字段: %r" % extra)
    for key, value in want.items():
        if kept.get(key) != value:
            sys.exit("状态文件 %s=%r，与自检判定 %r 不一致"
                     % (key, kept.get(key), value))
    return 0


MODES = {"json": check_json, "lines": check_lines, "status": check_status}


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    func = MODES.get(mode)
    if func is None:
        print("用法: llm_contract.py (json|lines|status) ...", file=sys.stderr)
        return 2
    return func(argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
