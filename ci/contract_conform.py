#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
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
# 每类判据各自成函数、只读「契约册」与「代码投影」两个入参，新增一节
# 契约时改一处即可；`check_all` 只负责汇总，不含任何具体判据。
#
# 用法: python3 ci/contract_conform.py [--root DIR]
# 退出码: 0 通过；1 有分叉；5 参数不满足或契约册缺失。
# ===========================================================================

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENUMS = ("source", "trust", "payload-kind", "session-mode")
BLOCK = re.compile(r"```contract\n(.*?)```", re.S)


class Broken(Exception):
    """契约册或代码投影读不出来，属于门禁自身无法判定。"""


@dataclass
class Book:
    """契约册里各小节的机读内容；小节名到字段的映射由 `SECTIONS` 决定。"""

    fields: dict[str, str] = field(default_factory=dict)
    outcome: dict[str, str] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)
    errors: list[tuple[str, int]] = field(default_factory=list)
    trust: dict[str, list[str]] = field(default_factory=dict)
    limits: dict[str, str] = field(default_factory=dict)
    l1: dict[str, list[str]] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    programs: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class Code:
    """代码投影里与契约对照所需的全部事实，只由 `projection` 填充。"""

    schema: str
    trust: str
    structs: dict[str, list[tuple[str, str, bool]]]
    source: list[tuple[str, str]]
    levels: list[tuple[str, str]]


@dataclass
class Tree:
    """投影文件在仓库里的位置；换根时整体重建，不逐项改全局。"""

    docs: Path
    schema: Path
    trust: Path
    status: Path
    event: Path
    source: Path
    escape: Path
    frame: Path
    exec: Path
    normal: Path
    approve: Path

    @classmethod
    def at(cls, root: Path) -> "Tree":
        base = root / "system"
        crates = base / "tui" / "crates"
        return cls(
            docs=base / "contracts",
            schema=crates / "intent" / "src" / "schema.rs",
            trust=crates / "intent" / "src" / "trust.rs",
            status=crates / "intent" / "src" / "status.rs",
            event=crates / "boot-tui" / "src" / "input" / "event.rs",
            source=crates / "boot-tui" / "src" / "input" / "source.rs",
            escape=crates / "boot-tui" / "src" / "input" / "escape.rs",
            frame=crates / "boot-tui" / "src" / "frame.rs",
            exec=crates / "airy-tui-exec" / "src" / "exec.rs",
            normal=crates / "airy-tui-exec" / "src" / "normalize.rs",
            approve=crates / "airy-tui-exec" / "src" / "approve.rs",
        )


TREE = Tree.at(ROOT)


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


def pairs(lines: list[str], where: str) -> list[tuple[str, str]]:
    return [split_kv(line, where) for line in lines]


def read_docs() -> dict[str, dict[str, list[str]]]:
    """把三份契约册里的 contract 代码块读成「文件 → 小节 → 行」。"""
    if not TREE.docs.is_dir():
        raise Broken(f"缺少契约册目录: {TREE.docs}")
    docs = {path.name: read_book(path) for path in sorted(TREE.docs.glob("*.md"))}
    if not docs:
        raise Broken(f"{TREE.docs} 下没有契约册")
    return docs


def read_book(path: Path) -> dict[str, list[str]]:
    """读一份契约册；未闭合的 contract 代码块按门禁不可判定处理。"""
    text = read_text(path)
    bodies = BLOCK.findall(text)
    if text.count("```contract") != len(bodies):
        raise Broken(f"{path.name}: contract 代码块未闭合")
    blocks: dict[str, list[str]] = {}
    for body in bodies:
        read_block(path, body, blocks)
    return blocks


def read_block(path: Path, body: str, blocks: dict[str, list[str]]) -> None:
    section = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise Broken(f"{path.name}: 空小节名")
            blocks.setdefault(section, [])
        elif section is None:
            raise Broken(f"{path.name}: 小节之前出现内容: {line!r}")
        else:
            blocks[section].append(line)


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


def rename_mode(text: str, start: int) -> str | None:
    """枚举行上方三行内的 `rename_all`；没有则按变体原名。"""
    head = "\n".join(text[:start].rstrip().splitlines()[-3:])
    mode = re.search(r'rename_all = "([^"]+)"', head)
    return mode.group(1) if mode else None


def variants(body: str, mode: str | None) -> list[tuple[str, str]]:
    """逐个变体取「变体名, 线名」；带字段的变体整段跳过。"""
    out: list[tuple[str, str]] = []
    pending: str | None = None
    depth = 0
    for raw in body.splitlines():
        line = raw.strip()
        if depth:
            depth += line.count("{") - line.count("}")
            continue
        if not line or line.startswith("//"):
            continue
        if line.startswith("#["):
            rename = re.search(r'rename = "([^"]+)"', line)
            pending = rename.group(1) if rename else pending
            continue
        name = re.match(r"([A-Za-z]\w*)", line)
        if not name:
            continue
        brace = line.count("{") - line.count("}")
        depth = brace if brace > 0 else 0
        out.append((name.group(1), pending or wire_name(name.group(1), mode)))
        pending = None
    return out


def parse_enums(text: str) -> dict[str, list[tuple[str, str]]]:
    """解析枚举及其线格式；返回「枚举 → [(变体, 线名)]」。"""
    out: dict[str, list[tuple[str, str]]] = {}
    for match in re.finditer(r"^pub enum (\w+) \{", text, re.M):
        start = match.end()
        close = text.find("\n}", start)
        if close < 0:
            raise Broken(f"枚举 {match.group(1)} 未闭合")
        out[match.group(1)] = variants(text[start:close], rename_mode(text, start))
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
        for field_name, ftype, default in structs[name]:
            path = f"{prefix}.{field_name}" if prefix else field_name
            optional = default or ftype.strip().startswith("Option<")
            out[path] = "optional" if optional else "required"
            sub = inner(ftype)
            if sub in structs:
                walk(sub, path)

    walk("Intent", "")
    return out


def put_states(table: dict[str, str], lines: list[str], where: str) -> None:
    for line in lines:
        key, val = split_kv(line, where)
        if val not in ("required", "optional"):
            raise Broken(f"{where}: 必填性只能是 required/optional: {line!r}")
        if key in table:
            raise Broken(f"{where}: 字段重复声明: {key}")
        table[key] = val


def put_fields(book: Book, lines: list[str], where: str) -> None:
    put_states(book.fields, lines, where)


def put_outcome(book: Book, lines: list[str], where: str) -> None:
    put_states(book.outcome, lines, where)


def put_meta(book: Book, lines: list[str], where: str) -> None:
    book.meta.update(dict(pairs(lines, where)))


def error_rows(lines: list[str], where: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"-\d+", parts[1]):
            raise Broken(f"{where}: 期望「状态 负返回码」: {line!r}")
        out.append((parts[0], int(parts[1])))
    return out


def put_errors(book: Book, lines: list[str], where: str) -> None:
    book.errors.extend(error_rows(lines, where))


def put_trust(book: Book, lines: list[str], where: str) -> None:
    book.trust.update({key: val.split() for key, val in pairs(lines, where)})


def put_limits(book: Book, lines: list[str], where: str) -> None:
    book.limits.update(dict(pairs(lines, where)))


def put_l1(book: Book, lines: list[str], where: str) -> None:
    for key, val in pairs(lines, where):
        book.l1.setdefault(key, []).append(val)


def put_enum(book: Book, section: str, lines: list[str], where: str) -> None:
    name = section[len("enum:"):]
    if name not in ENUMS:
        raise Broken(f"{where}: 未知取值集合 {name}")
    book.enums[name] = lines


SECTIONS = {
    "fields": put_fields,
    "outcome": put_outcome,
    "meta": put_meta,
    "errors": put_errors,
    "trust": put_trust,
    "limits": put_limits,
    "l1": put_l1,
    "programs": lambda book, lines, where: setattr(book, "programs", lines),
    "reasons": lambda book, lines, where: setattr(book, "reasons", lines),
}


def take(book: Book, section: str, lines: list[str], where: str) -> None:
    if section.startswith("enum:"):
        put_enum(book, section, lines, where)
        return
    handler = SECTIONS.get(section)
    if handler is None:
        raise Broken(f"{where}: 未知小节")
    handler(book, lines, where)


def projection() -> Code:
    schema = read_text(TREE.schema)
    trust = read_text(TREE.trust)
    enums = parse_enums(trust)
    return Code(
        schema=schema,
        trust=trust,
        structs=parse_structs(schema),
        source=enums.get("SourceKind", []),
        levels=enums.get("Trust", []),
    )


def check_meta(book: Book, code: Code) -> list[str]:
    fails = []
    if book.meta.get("schema") != const_str(code.schema, "SCHEMA"):
        fails.append(f"schema: 契约 {book.meta.get('schema')!r} 与代码不一致")
    if book.meta.get("trace_hex_len") != const_of(code.schema, "HEX_LEN"):
        fails.append(f"trace_hex_len: 契约 {book.meta.get('trace_hex_len')!r} 与代码不一致")
    return fails


def check_enums(book: Book, code: Code) -> list[str]:
    code_enums = {
        "source": [wire for _, wire in code.source],
        "trust": [wire for _, wire in code.levels],
        "payload-kind": [wire for _, wire in parse_enums(code.schema).get("PayloadKind", [])],
        "session-mode": [wire for _, wire in parse_enums(code.schema).get("SessionMode", [])],
    }
    return [
        f"enum:{name}: 契约 {book.enums.get(name)} 与代码 {code_enums[name]} 不一致"
        for name in ENUMS
        if book.enums.get(name) != code_enums[name]
    ]


def check_trust(book: Book, code: Code) -> list[str]:
    sources = {variant: wire for variant, wire in code.source}
    levels = {variant: wire for variant, wire in code.levels}
    code_trust = {
        wire: [levels.get(level, level) for level in allowed]
        for wire, allowed in allows_map(code.trust, sources).items()
    }
    if book.trust == code_trust:
        return []
    return [f"trust: 契约 {book.trust} 与代码 {code_trust} 不一致"]


def check_fields(book: Book, code: Code) -> list[str]:
    fails: list[str] = []
    code_fields = reachable(code.structs)
    missing = sorted(set(code_fields) - set(book.fields))
    extra = sorted(set(book.fields) - set(code_fields))
    if missing:
        fails.append(f"fields: 代码有、契约缺: {', '.join(missing)}")
    if extra:
        fails.append(f"fields: 契约有、代码缺: {', '.join(extra)}")
    for path in sorted(set(book.fields) & set(code_fields)):
        if book.fields[path] != code_fields[path]:
            fails.append(
                f"fields.{path}: 契约 {book.fields[path]} 与代码 {code_fields[path]} 不一致"
            )
    return fails


def check_errors(book: Book, code: Code) -> list[str]:
    rows = meta_rows(read_text(TREE.status))
    if book.errors == rows:
        return []
    return [f"errors: 契约 {book.errors} 与代码 {rows} 不一致"]


def check_l1(book: Book, code: Code) -> list[str]:
    fails: list[str] = []
    event = read_text(TREE.event)
    if book.l1.get("event") != ["RawEvent"]:
        fails.append(f"l1.event: 只能是 RawEvent，契约给的是 {book.l1.get('event')}")
    code_event = [name for name, _, _ in parse_structs(event).get("RawEvent", [])]
    if book.l1.get("field") != code_event:
        fails.append(f"l1.field: 契约 {book.l1.get('field')} 与代码 {code_event} 不一致")
    code_methods = parse_trait(read_text(TREE.source), "InputSource")
    if book.l1.get("method") != code_methods:
        fails.append(f"l1.method: 契约 {book.l1.get('method')} 与代码 {code_methods} 不一致")
    if book.l1.get("adapter") != ["InputSource"]:
        fails.append(f"l1.adapter: 只能是 InputSource，契约给的是 {book.l1.get('adapter')}")
    code_body = [variant for variant, _ in parse_enums(event).get("RawBody", [])]
    if book.l1.get("body") != code_body:
        fails.append(f"l1.body: 契约 {book.l1.get('body')} 与代码 {code_body} 不一致")
    return fails


def check_limits(book: Book, code: Code) -> list[str]:
    fails: list[str] = []
    if book.limits.get("escape_pending") != const_of(read_text(TREE.escape), "MAX_PENDING"):
        fails.append("limits.escape_pending: 与 MAX_PENDING 不一致")
    if book.limits.get("batch_max") != const_of(read_text(TREE.frame), "MAX_BATCH"):
        fails.append("limits.batch_max: 与 MAX_BATCH 不一致")
    return fails


def check_outcome(book: Book, code: Code) -> list[str]:
    struct = parse_structs(read_text(TREE.normal)).get("Outcome", [])
    code_outcome = {
        name: "optional" if default or ftype.startswith("Option<") else "required"
        for name, ftype, default in struct
    }
    if book.outcome == code_outcome:
        return []
    return [f"outcome: 契约 {book.outcome} 与代码 {code_outcome} 不一致"]


def check_programs(book: Book, code: Code) -> list[str]:
    code_progs = re.findall(r'\("([a-z0-9-]+)", Prog::\w+\)', read_text(TREE.exec))
    if book.programs == code_progs:
        return []
    return [f"programs: 契约 {book.programs} 与代码 {code_progs} 不一致"]


def check_reasons(book: Book, code: Code) -> list[str]:
    approve_rs = read_text(TREE.approve)
    body = re.search(r"pub fn reason\(self\) -> &'static str \{(.*?)\n    \}", approve_rs, re.S)
    if not body:
        return ["reasons: 找不到 Refusal::reason"]
    code_reasons = re.findall(r'=> "([^"]+)"', body.group(1))
    if book.reasons == code_reasons:
        return []
    return [f"reasons: 契约 {book.reasons} 与代码 {code_reasons} 不一致"]


CHECKS = (
    check_meta,
    check_enums,
    check_trust,
    check_fields,
    check_errors,
    check_l1,
    check_limits,
    check_outcome,
    check_programs,
    check_reasons,
)


def check_all(docs: dict[str, dict[str, list[str]]]) -> list[str]:
    book = Book()
    for doc, blocks in docs.items():
        for section, lines in blocks.items():
            take(book, section, lines, f"{doc}[{section}]")
    code = projection()
    fails: list[str] = []
    for check in CHECKS:
        fails.extend(check(book, code))
    return fails


def configure(root: Path) -> None:
    """换根时重建一张投影表；逐项改全局会在换根时漏掉某一行。"""
    global TREE
    TREE = Tree.at(root)


def main() -> int:
    args = sys.argv[1:]
    if args:
        if len(args) != 2 or args[0] != "--root":
            print("用法: python3 ci/contract_conform.py [--root DIR]", file=sys.stderr)
            return 5
        configure(Path(args[1]).resolve())
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
