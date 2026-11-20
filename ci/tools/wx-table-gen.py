#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tools/wx-table-gen.py — W^X 例外清单到内核只读表的生成器（K-02 / I-27）
#
# 用法:
#   python3 ci/tools/wx-table-gen.py [--toml FILE] [--out FILE]
#   python3 ci/tools/wx-table-gen.py --check
#
# 输入是 distro/lockfile/wx-exceptions.toml（唯一可编辑源，构建期输入，
# 不随镜像安装）；输出是 kernel/security/airy/airy_wx_table.c（只读
# struct airy_policy_table，编进 airy LSM）。内核不解析 TOML，运行期也
# 不接受例外登记：要放行新的身份只能改输入、重跑本工具、走评审。
#
# 输出是确定的——不写时间戳，同样的输入逐字节得到同样的结果，因此
# --check 能用逐字节比较断言「生成文件没有被手改」。
#
# 输入约束（违反即退出码 2，不落半成品）：
#   · schema 必须为 1，未知顶层键判错，防止把段落名拼错后静默失效；
#   · 每条例外必须写 agent_id——不写身份等于给所有任务开口子；
#   · req_prot / old_prot 取 0..7（R=1 W=2 X=4），agent_id 取 0..1023；
#   · name 必填且唯一，只允许 [a-z0-9][a-z0-9._-]*，因为它会写进 C 注释。
#
# 退出码: 0 生成成功或 --check 一致；1 --check 不一致（附差异）；2 输入、
#         参数或环境不合法。
# ===========================================================================

from __future__ import annotations

import argparse
import difflib
import re
import sys
import tomllib
from pathlib import Path

EXIT_OK = 0
EXIT_STALE = 1
EXIT_INPUT = 2

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "distro/lockfile/wx-exceptions.toml"
OUT = ROOT / "kernel/security/airy/airy_wx_table.c"
SCHEMA = 1
HOOK = "file_mprotect"
PROT_MAX = 7
AGENT_MAX = 1024
NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
FIELDS = ("req_prot", "old_prot", "agent_id")
ENTRY_KEYS = frozenset(("name", "agent_id", "req_prot", "old_prot"))
TOP_KEYS = frozenset(("schema", "exception"))

BANNER = """\
// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_wx_table.c — W^X 例外表，由 ci/tools/wx-table-gen.py 生成。
 *
 * 不要手改本文件：唯一可编辑源是 distro/lockfile/wx-exceptions.toml，
 * CI 以 wx-table-gen.py --check 逐字节断言两者一致。
 *
 * 表只装 ALLOW 行：命中的请求由拒绝转为受理；未命中落回
 * default_verdict = DENY，因此空清单也是 fail-closed 的，而每一条例外
 * 都能在同一处被评审。
 *
 * SSoT: 110-security/10-policy-compiler.md §7;
 *       0.0.5 施工图纸 §14.11。
 */

#include <linux/kernel.h>

#include <linux/airymax/security_types.h>
#include <airymax/airy_policy.h>

static const char *const wx_flds[] = {
	"req_prot",
	"old_prot",
	"agent_id",
};

"""

EMPTY_ROWS = """\
/*
 * 源清单为空：唯一的行不设谓词并给出 DENY，与 default_verdict 相同。
 * 注册面要求 row_count 非零（airy_policy.c 的 pol_meta_ok()），空数组
 * 表达不了「表存在但没有例外」，因此保留这一行。
 */
static const struct airy_policy_row wx_rows[] = {
	{ .conds = NULL, .cond_count = 0,
	  .verdict = AIRY_VERDICT_DENY, .fault = 0 },
};
"""

TABLE = """
const struct airy_policy_table airy_wx_table = {
	.name = "wx-exceptions",
	.hook = "%s",
	.rows = wx_rows,
	.row_count = ARRAY_SIZE(wx_rows),
	.field_count = ARRAY_SIZE(wx_flds),
	.field_names = wx_flds,
	.default_verdict = AIRY_VERDICT_DENY,
};
""" % HOOK


class BadInput(Exception):
    """输入不满足生成契约，携带可打印的定位信息。"""


def _need(cond: bool, where: str, why: str) -> None:
    """把校验失败统一转成带定位的 BadInput。"""
    if not cond:
        raise BadInput(f"{where}: {why}")


def _entry(raw: object, index: int) -> dict:
    """校验一条例外并归一成 {name, agent_id, req_prot?, old_prot?}。"""
    where = f"exception[{index}]"
    _need(isinstance(raw, dict), where, "必须是表")
    extra = set(raw) - ENTRY_KEYS
    _need(not extra, where, f"未知键 {sorted(extra)}")
    name = raw.get("name")
    _need(isinstance(name, str) and NAME.fullmatch(name), where,
          "name 缺失或非法（允许 [a-z0-9][a-z0-9._-]*）")
    agent = raw.get("agent_id")
    _need(isinstance(agent, int) and not isinstance(agent, bool), where,
          "agent_id 必填且为整数")
    _need(0 <= agent < AGENT_MAX, where,
          f"agent_id 越界 {agent}（0..{AGENT_MAX - 1}）")
    out = {"name": name, "agent_id": agent}
    for key in ("req_prot", "old_prot"):
        if key in raw:
            value = raw[key]
            _need(isinstance(value, int) and not isinstance(value, bool),
                  where, f"{key} 必须是整数")
            _need(0 <= value <= PROT_MAX, where,
                  f"{key} 越界 {value}（0..{PROT_MAX}）")
            out[key] = value
    return out


def load(path: Path) -> list[dict]:
    """读入并校验清单，返回按输入顺序排列的例外。"""
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except OSError as exc:
        raise BadInput(f"{path.name}: {exc.strerror}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BadInput(f"{path.name}: TOML 语法错误: {exc}") from exc
    extra = set(doc) - TOP_KEYS
    _need(not extra, path.name, f"未知顶层键 {sorted(extra)}")
    schema = doc.get("schema")
    _need(isinstance(schema, int) and not isinstance(schema, bool)
          and schema == SCHEMA, path.name, f"schema 必须是 {SCHEMA}")
    raw = doc.get("exception", [])
    _need(isinstance(raw, list), path.name, "exception 必须是表数组")
    items = [_entry(item, i) for i, item in enumerate(raw)]
    names = [item["name"] for item in items]
    dup = sorted({n for n in names if names.count(n) > 1})
    _need(not dup, path.name, f"name 重复 {dup}")
    return items


def _comment(entry: dict) -> str:
    """生成一行的溯源注释：名字 + 生效的身份与保护位。"""
    bits = [f"agent_id={entry['agent_id']}"]
    for key in ("req_prot", "old_prot"):
        if key in entry:
            bits.append(f"{key}=0x{entry[key]:x}")
    return f"/* {entry['name']}: {' '.join(bits)} */"


def _conds(entry: dict) -> list[tuple[int, int]]:
    """按选择性从高到低排条件，生成时字段顺序即评审时的阅读顺序。"""
    return [(FIELDS.index(key), entry[key])
            for key in ("agent_id", "req_prot", "old_prot") if key in entry]


def _block(entry: dict, index: int) -> str:
    """生成一条例外的条件数组与注释。"""
    lines = [_comment(entry),
             f"static const struct airy_policy_cond wx_c{index}[] = {{"]
    for field, value in _conds(entry):
        lines.append("\t{ .field = %d, .op = AIRY_CMP_EQ, .value = 0x%x },"
                     % (field, value))
    lines.append("};")
    return "\n".join(lines) + "\n\n"


def _rows(items: list[dict]) -> str:
    """生成行数组；空清单走保留一行的 fail-closed 形态。"""
    if not items:
        return EMPTY_ROWS
    parts = [_block(item, i) for i, item in enumerate(items)]
    parts.append("static const struct airy_policy_row wx_rows[] = {\n")
    for i in range(len(items)):
        parts.append("\t{ .conds = wx_c%d, .cond_count = ARRAY_SIZE(wx_c%d),\n"
                     "\t  .verdict = AIRY_VERDICT_ALLOW, .fault = 0 },\n"
                     % (i, i))
    parts.append("};\n")
    return "".join(parts)


def render(items: list[dict]) -> str:
    """拼出完整的生成文件内容。"""
    return BANNER + _rows(items) + TABLE


def _shown(path: Path) -> str:
    """仓内路径显示相对名，仓外路径（夹具）原样显示。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _diff(old: str, new: str, out: Path) -> str:
    """输出可读差异，路径一律用仓内相对名。"""
    return "".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile=_shown(out), tofile="重新生成", n=2))


def emit(text: str, count: int, out: Path, check: bool) -> int:
    """写盘或断言一致；--check 只读，绝不改动生成文件。"""
    try:
        old = out.read_text(encoding="utf-8")
    except OSError:
        old = ""
    if check:
        if old == text:
            print("一致 %s" % _shown(out))
            return EXIT_OK
        sys.stderr.write(_diff(old, text, out))
        sys.stderr.write("生成文件与清单不一致，重跑本工具（不带 --check）\n")
        return EXIT_STALE
    out.write_text(text, encoding="utf-8")
    print("已生成 %s（%d 条例外）" % (_shown(out), count))
    return EXIT_OK


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--toml", default=str(SRC), metavar="FILE")
    ap.add_argument("--out", default=str(OUT), metavar="FILE")
    ap.add_argument("--check", action="store_true",
                    help="只断言输出与输入一致，不写盘")
    args = ap.parse_args(argv)
    try:
        items = load(Path(args.toml))
        return emit(render(items), len(items), Path(args.out), args.check)
    except BadInput as exc:
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
