#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/quality_metrics.py — 代码度量棘轮（重复率与圈复杂度）
#
# 用法: python3 ci/quality_metrics.py --check [--dir DIR ...] [--base FILE]
#       python3 ci/quality_metrics.py --update [--dir DIR ...] [--base FILE]
#
# 0.0.2 与 0.0.3 的方案都把「重复率 < 3%、圈复杂度 < 2.5」写进 QG-01/QG-02，
# 但没人实现过判定——没有判据的目标值等于没有目标。0.0.4 把它落成棘轮式门禁：
#
#   · 未登记的函数，单函数圈复杂度不得超过 LIMIT_CC（10，Sonar 默认上限）；
#   · 未登记的重复片段（连续 WINDOW 行规范化窗口）一律判红；
#   · 存量超标函数与重复窗口按基线登记，只减不增：--update 拒绝抬高数量。
#
# 为什么不直接按 2.5 判全树：本仓自有代码实测圈复杂度均值 3.70、超 10 的函数
# 116 个，一次压到 2.5 只能靠机械拆函数，与「边开发边测试、稳定性优先」相冲突。
# 棘轮让新增代码不再恶化，存量靠后续重构消化，且差额每次运行都打印出来，
# 不会因为「没判据」而悄悄回到原点。
#
# 口径与边界：
#   · 只量 shell 与 python——与 ci/name_lint.py 同一交付面（ci tools distro
#     system），kernel 是上游树、不参与本仓度量（ALK-01 亦要求内核侧零改动）；
#   · tests/fixture 不量：那里的重复与分支是刻意构造的负例；
#   · 注释不算代码：shell 的整行 `#` 与 python 的文档字符串都剔除。规范要求
#     每个文件带中文文件头，若把它计进重复率，压低数字的唯一办法就是删掉
#     规范要求的文件头——度量不该逼人违规；
#   · 圈复杂度是判定点计数（McCabe）：python 走 AST（if/for/while/except/
#     ifexp/assert/comprehension/bool 运算），shell 走行内关键字与 &&/||；
#     嵌套定义的判定点只计入它自己，不摊给外层；
#   · 重复窗口按内容登记：改名会让旧窗口退役、新窗口登记，因此 --update
#     只比总量——数量不增即可换登记，数量增加即判红。
#
# 退出码: 0 通过（或已更新基线）；1 有判据不满足；2 参数不合法。
# ===========================================================================
import argparse
import ast
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

LIMIT_CC = 10
WINDOW = 6
TARGET_CC = 2.5
TARGET_DUP = 0.03
DEFAULT = ("ci", "tools", "distro", "system")
SKIP = frozenset(("tests", "fixture", "__pycache__", ".git", "node_modules"))
SUFFIX = (".sh", ".py")
PY_DEF = (ast.FunctionDef, ast.AsyncFunctionDef)
# 可携带文档字符串的节点：模块、类、函数。
DOC_NODES = PY_DEF + (ast.Module, ast.ClassDef)
PY_STEP = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp,
           ast.Assert, ast.ExceptHandler)
SH_DEF = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)")
SH_STEP = re.compile(r"(?<![\w-])(?:if|elif|for|while|until|case)(?![\w-])|&&|\|\|")
SCAFFOLD = frozenset(("fi", "done", "then", "else", "esac", "do", "{", "}"))


def sources(base):
    """遍历待度量源码，跳过 tests/fixture 等负例目录。"""
    stack = [Path(base)]
    while stack:
        cur = stack.pop()
        for ent in cur.iterdir():
            if ent.is_dir():
                if ent.name not in SKIP:
                    stack.append(ent)
            elif ent.suffix in SUFFIX and ent.is_file():
                yield ent


def py_cc(node):
    """判定点计数；嵌套定义的判定点不摊给外层。"""
    total = 0
    stack = list(ast.iter_child_nodes(node))
    while stack:
        cur = stack.pop()
        if isinstance(cur, PY_DEF):
            continue
        if isinstance(cur, PY_STEP):
            total += 1
        elif isinstance(cur, ast.BoolOp):
            total += len(cur.values) - 1
        elif isinstance(cur, ast.comprehension):
            total += 1 + len(cur.ifs)
        stack.extend(ast.iter_child_nodes(cur))
    return total


def doc_rows(tree):
    """文档字符串占据的行号集合。

    文档字符串是注释的 python 形态：项目规范要求每个文件带中文文件头，
    那是必须写的东西，不是可以合并的代码。把它算进重复率，唯一能压低
    数字的做法就是删掉规范要求的文件头——度量反而逼人违规，所以按注释
    剔除，与 shell 侧的整行 `#` 同一口径。
    """
    rows = set()
    for node in ast.walk(tree):
        if not isinstance(node, DOC_NODES) or not node.body:
            continue
        first = node.body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            rows.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return rows


def py_scan(path):
    """一次解析产出 (函数清单, 规范化行)；语法错误向上抛给调用点计缺陷。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text)
    got = [(n.name, 1 + py_cc(n))
           for n in ast.walk(tree) if isinstance(n, PY_DEF)]
    return got, norm(text.splitlines(), doc_rows(tree))


def sh_funcs(path):
    """行内关键字近似；`}` 顶格行视为函数结束，避免把尾部调用段计入最后一个函数。"""
    out, name, cc = [], None, 1
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        hit = SH_DEF.match(line)
        if hit:
            if name:
                out.append((name, cc))
            name, cc = hit.group(1), 1
            continue
        if name is None:
            continue
        if line.startswith("}"):
            out.append((name, cc))
            name, cc = None, 1
            continue
        if not line.lstrip().startswith("#"):
            cc += len(SH_STEP.findall(line))
    if name:
        out.append((name, cc))
    return out


def norm(rows, skip=()):
    """规范化：去空白、整行注释与脚手架行；skip 是剔掉的行号（文档字符串）。"""
    out = []
    for no, raw in enumerate(rows, 1):
        line = raw.strip()
        if no in skip:
            continue
        if line and not line.startswith("#") and line not in SCAFFOLD:
            out.append(line)
    return out


def code_lines(path):
    """shell 侧的有效行；python 的文档字符串处理见 py_scan。"""
    return norm(path.read_text(encoding="utf-8", errors="replace").splitlines())


def windows(lines):
    """连续 WINDOW 行窗口的摘要，用于重复片段登记。"""
    for i in range(len(lines) - WINDOW + 1):
        blob = "\n".join(lines[i:i + WINDOW])
        yield hashlib.sha1(blob.encode("utf-8")).hexdigest()


def scan(roots):
    """返回 (函数清单, 重复窗口计数, 有效行数, 缺陷)。"""
    funcs, dup, lines, bad = [], Counter(), 0, 0
    for base in roots:
        for path in sources(base):
            try:
                if path.suffix == ".py":
                    got, rows = py_scan(path)
                else:
                    got, rows = sh_funcs(path), code_lines(path)
            except SyntaxError as exc:
                print("%s:%s: E002 cannot parse (%s)" % (path, exc.lineno, exc.msg),
                      file=sys.stderr)
                bad += 1
                continue
            lines += len(rows)
            funcs += [(str(path), name, cc) for name, cc in got]
            dup.update(windows(rows))
    return funcs, dup, lines, bad


def over_names(funcs):
    """超出单函数上限的函数名集合（按名登记：文件与行号会漂移）。"""
    return sorted({name for _, name, cc in funcs if cc > LIMIT_CC})


def dup_hashes(dup):
    return {h for h, n in dup.items() if n > 1}


def report(funcs, dup, lines, base):
    """打印度量、目标差额与最差点位。"""
    total_cc = sum(cc for _, _, cc in funcs) or 1
    mean = float(total_cc) / max(1, len(funcs))
    dupl = sum(n - 1 for n in dup.values()) * WINDOW
    rate = float(dupl) / max(1, lines)
    print("度量: 函数 %d 个，有效行 %d 行" % (len(funcs), lines))
    print("  圈复杂度均值 %.2f（目标 < %.1f），超 %d 的函数 %d 个（基线 %d 个）"
          % (mean, TARGET_CC, LIMIT_CC, len(over_names(funcs)), len(base["over"])))
    print("  重复率 %.2f%%（目标 < %.0f%%），重复窗口 %d 个（基线 %d 个）"
          % (rate * 100, TARGET_DUP * 100, len(dup_hashes(dup)), len(base["dup"])))
    top = sorted(funcs, key=lambda f: -f[2])[:5]
    for path, name, cc in top:
        print("  最复杂: %s:%s cc=%d" % (path, name, cc))


def seat(funcs):
    """函数名 -> (路径, 圈复杂度)，同一名字取最差的一处，便于报出落点。"""
    worst = {}
    for path, name, cc in funcs:
        if cc > worst.get(name, ("", 0))[1]:
            worst[name] = (path, cc)
    return worst


def check(funcs, dup, base):
    """判红项：新的超标函数名、新的重复窗口。"""
    fails = 0
    known = set(base["over"])
    worst = seat(funcs)
    for name in over_names(funcs):
        if name in known:
            continue
        path, cc = worst[name]
        fails += 1
        print("quality/cc: %s:%s cc=%d 超过 %d，且未在基线登记"
              % (path, name, cc, LIMIT_CC))
    known_dup = set(base["dup"])
    for hit in sorted(dup_hashes(dup) - known_dup):
        fails += 1
        print("quality/dup: 新的重复片段 %s（窗口 %d 行）" % (hit[:12], WINDOW))
    return fails


def update(funcs, dup, base, path, fresh):
    """只减不增：数量增加即拒绝，同量换登记（改名）放行；首次建档不受此限。"""
    over = over_names(funcs)
    hashes = sorted(dup_hashes(dup))
    fails = 0
    if not fresh and len(over) > len(base["over"]):
        fails += 1
        print("quality/cc: 超标函数 %d -> %d，基线只降不升"
              % (len(base["over"]), len(over)))
    if not fresh and len(hashes) > len(base["dup"]):
        fails += 1
        print("quality/dup: 重复窗口 %d -> %d，基线只降不升"
              % (len(base["dup"]), len(hashes)))
    if fails:
        return fails
    for name in sorted(set(over) - set(base["over"])):
        print("登记超标函数: %s" % name)
    for name in sorted(set(base["over"]) - set(over)):
        print("退役超标函数: %s" % name)
    data = {"version": 1, "window": WINDOW, "limit_cc": LIMIT_CC,
            "over": over, "dup": hashes}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print("基线已写入 %s" % path)
    return 0


def load(path):
    if not path.is_file():
        return {"version": 1, "window": WINDOW, "limit_cc": LIMIT_CC,
                "over": [], "dup": []}
    return json.loads(path.read_text(encoding="utf-8"))


def roots_of(args, root):
    if args.dir:
        return [Path(d) for d in args.dir]
    return [root / d for d in DEFAULT if (root / d).is_dir()]


def main(argv):
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(add_help=True, description="代码度量棘轮")
    ap.add_argument("--check", action="store_true", help="按基线判定（默认）")
    ap.add_argument("--update", action="store_true", help="重写基线，只降不升")
    ap.add_argument("--dir", action="append", default=[], help="待量目录，可重复")
    ap.add_argument("--base", default="", help="基线文件，默认 ci/quality-baseline.json")
    args = ap.parse_args(argv)

    base_path = Path(args.base) if args.base else root / "ci/quality-baseline.json"
    base = load(base_path)
    funcs, dup, lines, bad = scan(roots_of(args, root))
    report(funcs, dup, lines, base)
    if args.update:
        return update(funcs, dup, base, base_path, not base_path.exists())
    fails = check(funcs, dup, base) + bad
    if fails:
        print("quality: %d 项判据未满足（存量修复用重构，基线不抬高）" % fails,
              file=sys.stderr)
        return 1
    print("quality: 棘轮通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
