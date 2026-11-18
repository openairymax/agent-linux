#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/contract_conform.py — 契约册与代码投影的一致性判定（Q-02 / QG-02）
#
# 契约册在 system/contracts/，代码投影在 system/tui/crates/。两边各自演化时
# 最容易出现「文档写了、代码没改」与「代码改了、文档没跟上」两种漂移，而
# 单看任何一边都发现不了。本脚本只做一件事：把契约册里 contract 代码块的
# 机读部分逐项与代码常量、枚举、字段表对照，任一侧先改即判负。
#
# 判据刻意不做启发式匹配：字段必须完全相等（双向），枚举按声明顺序逐项
# 相等，错误表按行号逐行相等。少一个字段、换一个顺序都算分叉——契约不是
# 摘要，摘要允许省略，契约不允许。
#
# 用法: python3 ci/contract_conform.py [--root DIR]
# 退出码: 0 通过；1 有分叉；5 参数不满足或契约册缺失。
# ===========================================================================

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "system" / "contracts"
TUI = ROOT / "system" / "tui" / "crates"

SCHEMA_RS = TUI / "intent" / "src" / "schema.rs"
TRUST_RS = TUI / "intent" / "src" / "trust.rs"
STATUS_RS = TUI / "intent" / "src" / "status.rs"
EVENT_RS = TUI / "boot-tui" / "src" / "input" / "event.rs"
SOURCE_RS = TUI / "boot-tui" / "src" / "input" / "source.rs"
ESCAPE_RS = TUI / "boot-tui" / "src" / "input" / "escape.rs"
FRAME_RS = TUI / "boot-tui" / "src" / "frame.rs"
EXEC_RS = TUI / "airy-tui-exec" / "src" / "exec.rs"
NORMAL_RS = TUI / "airy-tui-exec" / "src" / "normalize.rs"
APPROVE_RS = TUI / "airy-tui-exec" / "src" / "approve.rs"

ENUMS = ("source", "trust", "payload-kind", "session-mode")


class Broken(Exception):
    """契约册或代码投影读不出来，属于门禁自身无法判定。"""


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Broken(f"读不到 {path}: {exc}") from exc


def split_kv(line: str, where: str) -> tuple[str, str]:
    if " = " not in line:
        raise Broken(f"{where}: 期望「键 = 值」: {line!r}")
    key, val = line.split(" = ", 1)
    key, val = key.strip(), val.strip()
    if not key or not val:
        raise Broken(f"{where}: 键或值为空: {line!r}")
    return key, val


def read_docs() -> dict[str, dict[str, list[str]]]:
    """把三份契约册里的 contract 代码块读成「文件 → 小节 → 行」。"""
    found: dict[str, dict[str, list[str]]] = {}
    if not DOCS.is_dir():
        raise Broken(f"缺少契约册目录: {DOCS}")
    for path in sorted(DOCS.glob("*.md")):
        blocks: dict[str, list[str]] = {}
        section = None
        inside = False
        for raw in read_text(path).splitlines():
            line = raw.strip()
            if not inside:
                if line == "```contract":
                    inside = True
                    section = None
                continue
            if line == "```":
                inside = False
                continue
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                if not section:
                    raise Broken(f"{path.name}: 空小节名")
                blocks.setdefault(section, [])
                continue
            if section is None:
                raise Broken(f"{path.name}: 小节之前出现内容: {line!r}")
            blocks[section].append(line)
        if inside:
            raise Broken(f"{path.name}: contract 代码块未闭合")
        found[path.name] = blocks
    if not found:
        raise Broken(f"{DOCS} 下没有契约册")
    return found


def parse_structs(text: str) -> dict[str, list[tuple[str, str, bool]]]:
    """解析顶层 `pub struct`：字段名、类型、是否带 serde 缺省。"""
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for match in re.finditer(r"^pub struct (\w+) \{\n(.*?)^\}", text, re.S | re.M):
        fields: list[tuple[str, str, bool]] = []
        pending: list[str] = []
        for raw in match.group(2).splitlines():
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("#["):
                pending.append(line)
                continue
            field = re.match(r"pub (\w+): (.+),$", line)
            if field:
                default = any("serde(default" in attr for attr in pending)
                fields.append((field.group(1), field.group(2), default))
                pending.clear()
        out[match.group(1)] = fields
    return out


def wire_name(ident: str, mode: str | None) -> str:
    if mode == "lowercase":
        return ident.lower()
    if mode == "kebab-case":
        return re.sub(r"(?<!^)(?=[A-Z])", "-", ident).lower()
    return ident


def parse_enums(text: str) -> dict[str, list[tuple[str, str]]]:
    """解析枚举及其线格式；返回「枚举 → [(变体, 线名)]」。"""
    out: dict[str, list[tuple[str, str]]] = {}
    for match in re.finditer(r"^pub enum (\w+) \{", text, re.M):
        start = match.end()
        close = text.find("\n}", start)
        if close < 0:
            raise Broken(f"枚举 {match.group(1)} 未闭合")
        head = "\n".join(text[: match.start()].rstrip().splitlines()[-3:])
        mode = re.search(r'rename_all = "([^"]+)"', head)
        mode = mode.group(1) if mode else None
        variants: list[tuple[str, str]] = []
        pending: str | None = None
        depth = 0
        for raw in text[start:close].splitlines():
            line = raw.strip()
            if depth:
                depth += line.count("{") - line.count("}")
                continue
            if not line or line.startswith("//"):
                continue
            if line.startswith("#["):
                rename = re.search(r'rename = "([^"]+)"', line)
                if rename:
                    pending = rename.group(1)
                continue
            name = re.match(r"([A-Za-z]\w*)", line)
            if not name:
                continue
            if line.count("{") > line.count("}"):
                depth = line.count("{") - line.count("}")
            variants.append((name.group(1), pending or wire_name(name.group(1), mode)))
            pending = None
        out[match.group(1)] = variants
    return out


def parse_trait(text: str, name: str) -> list[str]:
    match = re.search(rf"^pub trait {name}\b.*?\{{\n(.*?)^\}}", text, re.S | re.M)
    if not match:
        raise Broken(f"找不到 trait {name}")
    return re.findall(r"^\s*fn (\w+)\(", match.group(1), re.M)


def const_str(text: str, name: str) -> str:
    match = re.search(rf'^\s*pub const {name}: &str = "([^"]*)";', text, re.M)
    if not match:
        raise Broken(f"找不到常量 {name}")
    return match.group(1)


def const_of(text: str, name: str) -> str:
    match = re.search(rf"^\s*pub const {name}: (?:usize|u32|i32) = (\d+);", text, re.M)
    if not match:
        raise Broken(f"找不到常量 {name}")
    return match.group(1)


def meta_rows(text: str) -> list[tuple[str, int]]:
    consts = {
        name: int(value)
        for name, value in re.findall(r"^const (\w+): i32 = (-?\d+);", text, re.M)
    }
    rows = re.findall(r'Meta::new\("([^"]*)", -(\w+), "[^"]*"\)', text)
    if not rows:
        raise Broken("失败语义表为空")
    out = []
    for status, symbol in rows:
        if symbol not in consts:
            raise Broken(f"未定义的 errno 符号: {symbol}")
        out.append((status, -consts[symbol]))
    return out


def allows_map(text: str, sources: dict[str, str]) -> dict[str, list[str]]:
    match = re.search(r"pub fn allows\(self, trust: Trust\) -> bool \{(.*?)\n    \}", text, re.S)
    if not match:
        raise Broken("找不到 SourceKind::allows")
    out: dict[str, list[str]] = {}
    for line in match.group(1).splitlines():
        if "=>" not in line:
            continue
        lhs, rhs = line.split("=>", 1)
        kinds = re.findall(r"Self::(\w+)", lhs)
        levels = re.findall(r"Trust::(\w+)", rhs)
        for kind in kinds:
            if kind not in sources:
                raise Broken(f"allows 里的未知来源: {kind}")
            out[sources[kind]] = levels
    return out


def inner(ftype: str) -> str | None:
    """字段类型若是本文件内的结构体（可透过 Option），返回其名字。"""
    text = ftype.strip()
    option = re.fullmatch(r"Option<(.+)>", text)
    if option:
        text = option.group(1).strip()
    if text.startswith("Vec<"):
        return None
    plain = re.fullmatch(r"([A-Za-z]\w*)", text)
    return plain.group(1) if plain else None


def reachable(structs: dict[str, list[tuple[str, str, bool]]]) -> dict[str, str]:
    """从 Intent 出发的可达字段路径与局部必填性。"""
    out: dict[str, str] = {}

    def walk(name: str, prefix: str) -> None:
        if name not in structs:
            raise Broken(f"结构体 {name} 不存在")
        for field, ftype, default in structs[name]:
            path = f"{prefix}.{field}" if prefix else field
            optional = default or ftype.strip().startswith("Option<")
            out[path] = "optional" if optional else "required"
            sub = inner(ftype)
            if sub in structs:
                walk(sub, path)

    walk("Intent", "")
    return out


def check_all(docs: dict[str, dict[str, list[str]]]) -> list[str]:
    fails: list[str] = []
    fields: dict[str, str] = {}
    outcome: dict[str, str] = {}
    enums: dict[str, list[str]] = {}
    trust: dict[str, list[str]] = {}
    limits: dict[str, str] = {}
    l1: dict[str, list[str]] = {}
    errors: list[tuple[str, int]] = []
    programs: list[str] = []
    reasons: list[str] = []
    meta: dict[str, str] = {}

    def add_field(table: dict[str, str], line: str, where: str) -> None:
        key, val = split_kv(line, where)
        if val not in ("required", "optional"):
            raise Broken(f"{where}: 必填性只能是 required/optional: {line!r}")
        if key in table:
            raise Broken(f"{where}: 字段重复声明: {key}")
        table[key] = val

    for doc, blocks in docs.items():
        for section, lines in blocks.items():
            where = f"{doc}[{section}]"
            if section == "fields":
                for line in lines:
                    add_field(fields, line, where)
            elif section == "outcome":
                for line in lines:
                    add_field(outcome, line, where)
            elif section == "meta":
                for line in lines:
                    key, val = split_kv(line, where)
                    meta[key] = val
            elif section == "errors":
                for line in lines:
                    parts = line.split()
                    if len(parts) != 2 or not re.fullmatch(r"-\d+", parts[1]):
                        raise Broken(f"{where}: 期望「状态 负返回码」: {line!r}")
                    errors.append((parts[0], int(parts[1])))
            elif section == "trust":
                for line in lines:
                    key, val = split_kv(line, where)
                    trust[key] = val.split()
            elif section == "limits":
                for line in lines:
                    key, val = split_kv(line, where)
                    limits[key] = val
            elif section == "l1":
                for line in lines:
                    key, val = split_kv(line, where)
                    l1.setdefault(key, []).append(val)
            elif section.startswith("enum:"):
                name = section[len("enum:"):]
                if name not in ENUMS:
                    raise Broken(f"{where}: 未知取值集合 {name}")
                enums[name] = lines
            elif section == "programs":
                programs = lines
            elif section == "reasons":
                reasons = lines
            else:
                raise Broken(f"{where}: 未知小节")

    schema_rs = read_text(SCHEMA_RS)
    trust_rs = read_text(TRUST_RS)
    structs = parse_structs(schema_rs)
    source_enum = parse_enums(trust_rs).get("SourceKind", [])
    trust_enum = parse_enums(trust_rs).get("Trust", [])
    src_wire = {variant: wire for variant, wire in source_enum}
    trust_wire = {variant: wire for variant, wire in trust_enum}

    # 1. schema 与 trace_id 长度
    if meta.get("schema") != const_str(schema_rs, "SCHEMA"):
        fails.append(f"schema: 契约 {meta.get('schema')!r} 与代码不一致")
    if meta.get("trace_hex_len") != const_of(schema_rs, "HEX_LEN"):
        fails.append(f"trace_hex_len: 契约 {meta.get('trace_hex_len')!r} 与代码不一致")

    # 2. 四个取值集合
    code_enums = {
        "source": [wire for _, wire in source_enum],
        "trust": [wire for _, wire in trust_enum],
        "payload-kind": [wire for _, wire in parse_enums(schema_rs).get("PayloadKind", [])],
        "session-mode": [wire for _, wire in parse_enums(schema_rs).get("SessionMode", [])],
    }
    for name in ENUMS:
        if enums.get(name) != code_enums[name]:
            fails.append(
                f"enum:{name}: 契约 {enums.get(name)} 与代码 {code_enums[name]} 不一致"
            )

    # 3. 来源与信任级别配对
    code_trust = {
        wire: [trust_wire.get(level, level) for level in levels]
        for wire, levels in allows_map(trust_rs, src_wire).items()
    }
    if trust != code_trust:
        fails.append(f"trust: 契约 {trust} 与代码 {code_trust} 不一致")

    # 4. 字段表（双向）
    code_fields = reachable(structs)
    missing = sorted(set(code_fields) - set(fields))
    extra = sorted(set(fields) - set(code_fields))
    if missing:
        fails.append(f"fields: 代码有、契约缺: {', '.join(missing)}")
    if extra:
        fails.append(f"fields: 契约有、代码缺: {', '.join(extra)}")
    for path in sorted(set(fields) & set(code_fields)):
        if fields[path] != code_fields[path]:
            fails.append(
                f"fields.{path}: 契约 {fields[path]} 与代码 {code_fields[path]} 不一致"
            )

    # 5. 失败语义表
    status_rs = read_text(STATUS_RS)
    if errors != meta_rows(status_rs):
        fails.append(f"errors: 契约 {errors} 与代码 {meta_rows(status_rs)} 不一致")

    # 6. L1 事件、适配器与载荷
    event_rs = read_text(EVENT_RS)
    event_structs = parse_structs(event_rs)
    if l1.get("event") != ["RawEvent"]:
        fails.append(f"l1.event: 只能是 RawEvent，契约给的是 {l1.get('event')}")
    code_event = [name for name, _, _ in event_structs.get("RawEvent", [])]
    if l1.get("field") != code_event:
        fails.append(f"l1.field: 契约 {l1.get('field')} 与代码 {code_event} 不一致")
    code_methods = parse_trait(read_text(SOURCE_RS), "InputSource")
    if l1.get("method") != code_methods:
        fails.append(f"l1.method: 契约 {l1.get('method')} 与代码 {code_methods} 不一致")
    if l1.get("adapter") != ["InputSource"]:
        fails.append(f"l1.adapter: 只能是 InputSource，契约给的是 {l1.get('adapter')}")
    code_body = [variant for variant, _ in parse_enums(event_rs).get("RawBody", [])]
    if l1.get("body") != code_body:
        fails.append(f"l1.body: 契约 {l1.get('body')} 与代码 {code_body} 不一致")

    # 7. 输入上限
    escape_rs = read_text(ESCAPE_RS)
    if limits.get("escape_pending") != const_of(escape_rs, "MAX_PENDING"):
        fails.append("limits.escape_pending: 与 MAX_PENDING 不一致")
    if limits.get("batch_max") != const_of(read_text(FRAME_RS), "MAX_BATCH"):
        fails.append("limits.batch_max: 与 MAX_BATCH 不一致")

    # 8. 执行记录
    outcome_struct = parse_structs(read_text(NORMAL_RS)).get("Outcome", [])
    code_outcome = {
        name: "optional" if default or ftype.startswith("Option<") else "required"
        for name, ftype, default in outcome_struct
    }
    if outcome != code_outcome:
        fails.append(f"outcome: 契约 {outcome} 与代码 {code_outcome} 不一致")

    # 9. 白名单入口
    code_progs = re.findall(r'\("([a-z0-9-]+)", Prog::\w+\)', read_text(EXEC_RS))
    if programs != code_progs:
        fails.append(f"programs: 契约 {programs} 与代码 {code_progs} 不一致")

    # 10. 拒绝原因
    approve_rs = read_text(APPROVE_RS)
    body = re.search(r"pub fn reason\(self\) -> &'static str \{(.*?)\n    \}", approve_rs, re.S)
    if not body:
        fails.append("reasons: 找不到 Refusal::reason")
    else:
        code_reasons = re.findall(r'=> "([^"]+)"', body.group(1))
        if reasons != code_reasons:
            fails.append(f"reasons: 契约 {reasons} 与代码 {code_reasons} 不一致")

    return fails


def main() -> int:
    root = ROOT
    args = sys.argv[1:]
    if args:
        if len(args) == 2 and args[0] == "--root":
            global DOCS, TUI, SCHEMA_RS, TRUST_RS, STATUS_RS, EVENT_RS
            global SOURCE_RS, ESCAPE_RS, FRAME_RS, EXEC_RS, NORMAL_RS, APPROVE_RS
            root = Path(args[1]).resolve()
            DOCS = root / "system" / "contracts"
            TUI = root / "system" / "tui" / "crates"
            SCHEMA_RS = TUI / "intent" / "src" / "schema.rs"
            TRUST_RS = TUI / "intent" / "src" / "trust.rs"
            STATUS_RS = TUI / "intent" / "src" / "status.rs"
            EVENT_RS = TUI / "boot-tui" / "src" / "input" / "event.rs"
            SOURCE_RS = TUI / "boot-tui" / "src" / "input" / "source.rs"
            ESCAPE_RS = TUI / "boot-tui" / "src" / "input" / "escape.rs"
            FRAME_RS = TUI / "boot-tui" / "src" / "frame.rs"
            EXEC_RS = TUI / "airy-tui-exec" / "src" / "exec.rs"
            NORMAL_RS = TUI / "airy-tui-exec" / "src" / "normalize.rs"
            APPROVE_RS = TUI / "airy-tui-exec" / "src" / "approve.rs"
        else:
            print("用法: python3 ci/contract_conform.py [--root DIR]", file=sys.stderr)
            return 5
    try:
        fails = check_all(read_docs())
    except Broken as exc:
        print(f"contract-conform: {exc}", file=sys.stderr)
        return 5
    for item in fails:
        print(f"FAIL {item}", file=sys.stderr)
    if fails:
        print(f"contract-conform: {len(fails)} 处契约与代码不一致", file=sys.stderr)
        return 1
    print("契约册与代码投影一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
