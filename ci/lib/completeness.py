#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""ci/lib/completeness.py —— 功能归属册的三态对账

由 ci/gates/completeness.sh 调用，也可被回归脚本直接执行。

归属册是 docs-linux 功能面缺口的唯一记账处：每条缺口一行，归属只有三种——
已实现、0.0.5、移出（承接版本 X）。三者之外的行，以及 §3 统计表与明细表
对不上的行，都说明这本账已经回答不了「这个版本做了什么」，门禁据此判红。
「未归属」不是第四态，是账本损坏。

已实现行的证据列写的是仓库相对路径与门禁名，因此逐条落地校验：路径必须
能在工作树里打开，门禁必须在 ci/gates 下且带 `# 用法:` 头（否则
ci/ci.sh --list 看不到它）。锚点列同样落地到 docs-linux——一行缺口如果连
设计出处都指不到，它与「已实现」之间就没有可复核的距离。

发布模式（--release）多一条判据：账上不许再有 0.0.5。施工方案 §2.4 把
「登记缺口」与「移出范围」分开：留在账上却没做完的条目，发布前必须落成
已实现或移出（承接版本 X）之一，不接受「还没做」这个答复。
"""

from __future__ import annotations

import json
import os
import re
import sys

STATES = ("已实现", "0.0.5")
ROW = re.compile(r"^\| (CA-\d{3}) \|")
OUT = re.compile(r"^移出（([^）]+)）$")
STAT = re.compile(r"^\| (已实现|0\.0\.5|移出（承接版本）|合计) \| (\d+) \|$")
TOK = re.compile(r"`([^`]+)`")
BRACE = re.compile(r"\{([^{}]*)\}")
BARE = re.compile(r"^[a-z][a-z0-9-]*\.sh$")
PREFIX = ("ci/gates/", "system/", "services/", "distro/", "kernel/",
          "memory/", "cognition/", "cloudnative/", "tools/", "tests-linux/")
USAGE = "用法: completeness.py DOC SRC DOCS [--release] [--json]"


def die(msg):
    """判定失败：只留一句原因，调用方据此退出 1。"""
    print("completeness: " + msg, file=sys.stderr)
    raise SystemExit(1)


def read_txt(path):
    """按 UTF-8 读入；归属册与测试夹具都是纯文本。"""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def state_of(cell):
    """归属列取值；「移出」必须写明承接版本。

    没有承接版本的「移出」与「未归属」无法区分：条目既不在本版，也不知道
    去了哪里，正是本册拒绝出现的缺陷。
    """
    if cell in STATES:
        return cell, ""
    hit = OUT.match(cell)
    if hit and hit.group(1).strip():
        return "移出", hit.group(1).strip()
    die("归属列不在三态之内: " + cell[:40])


def parse_row(line, idx):
    """明细行固定五列：编号、功能面、锚点、归属、证据。"""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) != 5:
        die("第 %d 行不是五列: %s" % (idx, line[:40]))
    state, succ = state_of(cells[3])
    return {"id": cells[0], "anchor": cells[2], "state": state,
            "succ": succ, "ev": cells[4]}


def parse_rows(text):
    """取出全部明细行；编号重复说明同一本账被并列了两份。"""
    rows, seen = [], set()
    for idx, line in enumerate(text.splitlines(), 1):
        if not ROW.match(line):
            continue
        row = parse_row(line, idx)
        if row["id"] in seen:
            die("编号重复: " + row["id"])
        seen.add(row["id"])
        rows.append(row)
    if not rows:
        die("归属册没有 CA- 明细行")
    return rows


def sec_stats(text):
    """§3 统计表的申报值：状态标签与合计。"""
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith("## 3.")
            continue
        hit = STAT.match(line.replace("*", ""))
        if inside and hit:
            out[hit.group(1)] = int(hit.group(2))
    return out


def tally(rows):
    """按三态计数；合计即明细行数。"""
    got = {"已实现": 0, "0.0.5": 0, "移出": 0}
    for row in rows:
        got[row["state"]] += 1
    got["合计"] = len(rows)
    return got


def same_stats(want, got):
    """申报值与明细值逐项相等，缺行同样判红。"""
    pairs = (("已实现", "已实现"), ("0.0.5", "0.0.5"),
             ("移出（承接版本）", "移出"), ("合计", "合计"))
    for label, key in pairs:
        if label not in want:
            die("§3 统计表缺少「%s」行" % label)
        if want[label] != got[key]:
            die("§3 与明细不一致：%s 表 %d，明细 %d"
                % (label, want[label], got[key]))


def ev_tokens(ev):
    """证据列里带仓库前缀的路径与门禁名。"""
    return [t.strip() for t in TOK.findall(ev) if t.strip().startswith(PREFIX)]


def bare_sh(ev):
    """证据列里的裸脚本名：无法定位到文件，按 1.2 判红。"""
    return [t.strip() for t in TOK.findall(ev) if BARE.match(t.strip())]


def expand(tok):
    """展开 {a,b} 写法：一条证据可以指向多个同类文件。"""
    hit = BRACE.search(tok)
    if not hit:
        return [tok]
    head, tail = tok[:hit.start()], tok[hit.end():]
    out = []
    for part in hit.group(1).split(","):
        out.extend(expand(head + part.strip() + tail))
    return out


def gate_ok(path):
    """门禁必须存在且带 `# 用法:` 头，否则 ci/ci.sh --list 看不到它。"""
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        return any(line.startswith("# 用法:") for line in fh)


def row_paths(row, src):
    """单行的路径证据与门禁证据。"""
    bad = ["%s: 证据须写相对路径（%s）" % (row["id"], bare)
           for bare in bare_sh(row["ev"])]
    for tok in ev_tokens(row["ev"]):
        for path in expand(tok):
            full = os.path.join(src, path.rstrip("/"))
            if not os.path.exists(full):
                bad.append("%s: 缺 %s" % (row["id"], path))
            elif path.startswith("ci/gates/") and not gate_ok(full):
                bad.append("%s: %s 缺 # 用法: 头" % (row["id"], path))
    return bad


def anchor_bad(row, docs):
    """锚点必须能在 docs-linux 内打开，否则这一行没有设计出处。"""
    hit = TOK.search(row["anchor"])
    if not hit:
        return ["%s: 锚点列无路径" % row["id"]]
    path = os.path.join(docs, hit.group(1).strip())
    if os.path.isfile(path):
        return []
    return ["%s: 锚点缺 %s" % (row["id"], hit.group(1))]


def miss_rows(rows, src, docs):
    """全表逐行落地检查：路径、门禁、锚点三处。"""
    bad = []
    for row in rows:
        bad.extend(row_paths(row, src))
        bad.extend(anchor_bad(row, docs))
    return bad


def need_paths(rows, src, docs):
    bad = miss_rows(rows, src, docs)
    if bad:
        die("证据不落地 %d 条：%s" % (len(bad), "；".join(bad[:6])))


def need_closed(rows, got):
    """发布前不许留下「登记缺口」：已实现或移出（承接版本）二选一。"""
    if not got["0.0.5"]:
        return
    ids = " ".join(r["id"] for r in rows if r["state"] == "0.0.5")
    die("发布前仍有 %d 条 0.0.5：%s" % (got["0.0.5"], ids))


def audit(doc, src, docs, release):
    """一次完整对账，返回三态计数。"""
    text = read_txt(doc)
    rows = parse_rows(text)
    got = tally(rows)
    same_stats(sec_stats(text), got)
    need_paths(rows, src, docs)
    if release:
        need_closed(rows, got)
    return got


def main(argv):
    """命令行：三个位置参数（册、源码树、设计树）加两个开关。"""
    args = argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    flags = [a for a in args if a.startswith("--")]
    pos = [a for a in args if not a.startswith("--")]
    if len(pos) != 3 or set(flags) - {"--release", "--json"}:
        print(USAGE, file=sys.stderr)
        return 2
    got = audit(pos[0], pos[1], pos[2], "--release" in flags)
    if "--json" in flags:
        print(json.dumps({"rows": got["合计"], "impl": got["已实现"],
                          "open": got["0.0.5"], "out": got["移出"]},
                         ensure_ascii=False, sort_keys=True))
    else:
        print("rows=%d impl=%d open=%d out=%d"
              % (got["合计"], got["已实现"], got["0.0.5"], got["移出"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
