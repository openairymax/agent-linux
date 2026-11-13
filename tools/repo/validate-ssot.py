#!/usr/bin/env python3
"""
validate-ssot.py — SSoT Rule ID Consistency Validator

Validates that every OS-*-NNN (agent-linux) and *-NNN (agentrt) rule ID
referenced in documentation is registered in the SSoT YAML registry.

Usage:
    python3 tools/repo/validate-ssot.py <docs_root> <ssot_yaml>

Exit codes:
    0 — all rule IDs are registered (or only deprecated warnings)
    1 — one or more unregistered rule IDs found
    2 — YAML parsing or file error

Authority: docs/docs-linux/50-engineering-standards/09-ssot-registry.md
OS-IRON-015: All rule IDs must be registered; deprecated IDs are retained
             but marked as deprecated.
"""

import re
import sys
import os
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(2)


# ─── Rule ID Patterns ──────────────────────────────────────────────────────

# agent-linux rules: OS-<PREFIX>-<SUBDOMAIN>-NNN or OS-<PREFIX>-NNN
OS_PATTERN = re.compile(
    r'\bOS-'
    r'(?:'
    r'IRON|KER|STD-CODE|STD-FMT|STD-STY|STD-GOV|STD-RUST|STD-TOOL|'
    r'STD-PROD|STD-DOC|STD-SEC|STD-SPDX|STD-TEST|'
    r'BAN|ACC|ABI|SEC|ARCH|IFACE|TEST|'
    r'OPS|IPC|CHK-DOC|CHK-CODE|CHK-IRON|DEV|BUILD|DRV|OBS|MM|TST|FMT'
    r')'
    r'-?\d+(?:[~–]\d+)?'
    r'(?:-SP\d+|SP\d+|OS\d+)?'
    r'\b'
)

# agentrt rules: <PREFIX>-NNN (no OS- prefix)
AGENTRT_PATTERN = re.compile(
    r'\b(?:'
    r'IRON|BAN|STD|ACC|FOUND|SPLIT|PROD|ARC|LC|PRT|LOG|'
    r'PATH-BAN|L|CROSS|REQ|W|SP'
    r')-\d+(?:[~–]\d+)?'
    r'(?:-SP\d+|SP\d+|OS\d+)?'
    r'\b'
)

# OS-STD-030~057 (Rust rules use plain OS-STD-NNN format)
OS_STD_PLAIN_PATTERN = re.compile(r'\bOS-STD-\d{2,3}\b')


def _id_num(rid):
    """规则 ID 末段的数值；末段不是数字时为 None。"""
    m = re.search(r'(\d+)$', rid)
    return int(m.group(1)) if m else None


def _id_prefix(rid):
    """规则 ID 去掉末段数字后的前缀。"""
    m = re.match(r'(.+?)-\d+$', rid)
    return m.group(1) if m else rid


def _id_pad(rid):
    """规则 ID 末段数字的原始宽度；末段不是数字时按三位。"""
    m = re.search(r'(\d+)$', rid)
    return len(m.group(1)) if m else 3


def _expand_ids(start, start_num, end_num):
    """按起点宽度把区间展开为逐个 ID。"""
    pad = _id_pad(start)
    return [
        f"{_id_prefix(start)}-{str(n).zfill(pad)}"
        for n in range(start_num, end_num + 1)
    ]


def _expand(start, end):
    """把 OS-KER-001 ~ OS-KER-228 这样的区间展开为逐个 ID。

    零填充宽度取自起点串自身的末段数字：区间两侧宽度不一致时以起点为准，
    这样 ``001~228`` 得到三位编号，而 ``1~28`` 不会补成三位。
    """
    start_num = _id_num(start)
    end_num = _id_num(end)
    if start_num is None or end_num is None:
        return []
    return _expand_ids(start, start_num, end_num)


def _record(entry):
    """单个条目 → 登记记录。"""
    return {
        'status': entry.get('status', 'active'),
        'desc': entry.get('desc', ''),
        'section': entry.get('section', ''),
    }


def _add_range(registered, span):
    """区间条目：展开后的每个 ID 登记为 active，并标注来源区间。"""
    desc = f"Range entry: {span['start']} ~ {span['end']}"
    for eid in _expand(span['start'], span['end']):
        registered[eid] = {
            'status': 'active',
            'desc': desc,
            'section': span.get('section', ''),
        }


def _add_deprecated(registered, dep_id, dep_desc):
    """废弃 ID：已在表内改状态，未在表内补一条带说明的记录。"""
    if dep_id in registered:
        registered[dep_id]['status'] = 'deprecated'
        return
    registered[dep_id] = {
        'status': 'deprecated',
        'desc': dep_desc,
        'section': '',
    }


def _add_entries(entries, registered):
    """实体条目逐个登记。"""
    for entry in entries:
        registered[entry['id']] = _record(entry)


def _collect(group, registered, dep_desc):
    """把一个条目组并入登记表：实体条目、区间、废弃、离散条目。

    两个规则域（OS-* 与 agentrt）的组结构相同，差异只有「不在活动区间内
    的废弃 ID」的说明串，故由调用方给入，避免两份循环各自漂移。
    """
    _add_entries(group.get('entries', []), registered)
    for span in group.get('ranges', []):
        _add_range(registered, span)
    for dep_id in group.get('deprecated', []):
        _add_deprecated(registered, dep_id, dep_desc)
    _add_entries(group.get('individual_entries', []), registered)


def load_ssot(yaml_path):
    """Load the SSoT YAML registry and expand ranges into a full ID set."""
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    registered = {}  # id -> {status, desc, section}
    for group in data.get('agentrt_linux_rules', []):
        _collect(group, registered, 'Deprecated (not in active range)')
    for group in data.get('agentrt_rules', []):
        _collect(group, registered, 'Deprecated')
    return registered


def md_files(docs_root):
    """枚举待扫描的 .md 文件。

    优先使用 ``git ls-files``（仅被跟踪文件，天然排除子模块内容与未跟踪
    文件），使本地（子模块已检出）与 CI（子模块为空目录）行为一致；
    非 git 仓库时回退到递归遍历。
    """
    tracked = _git_md(docs_root)
    if tracked is not None:
        return tracked
    return [str(p) for p in Path(docs_root).rglob("*.md")]


def _ls_md(docs_root):
    """执行 ``git ls-files`` 枚举 .md 正文；非 git 仓库或失败时返回 None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", docs_root, "ls-files", "-z", "--", "*.md"],
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _git_md(docs_root):
    """``git ls-files`` 命中的 .md；非 git 仓库或命令失败时返回 None。"""
    raw = _ls_md(docs_root)
    if raw is None:
        return None
    return [os.path.join(docs_root, p) for p in raw.split("\0") if p]


def parse_range(token):
    """把形如 ``PREFIX-N~M`` 的区间记号展开为逐个 ID。

    文档中常以区间简写引用连续编号（如 ``OS-IRON-001~016``）。此类记号
    不是单个注册 ID，需按起始号位数补零展开后逐个比对。非区间记号原样
    返回单元素列表。
    """
    m = re.fullmatch(r'(?P<prefix>.+?)-(?P<start>\d+)[~–](?P<end>\d+)', token)
    if not m:
        return [token]
    prefix = m.group('prefix')
    start = m.group('start')
    pad = len(start)
    lo, hi = int(start), int(m.group('end'))
    if hi < lo:
        return [token]
    return [f'{prefix}-{str(n).zfill(pad)}' for n in range(lo, hi + 1)]


def _mark(used, rid, rel_path):
    """把一条规则引用（区间记号展开后）记入使用表。"""
    for eid in parse_range(rid):
        used.setdefault(eid, []).append(rel_path)


def _text_of(md_path):
    """读取文档正文；不可读时返回 None，由调用方跳过该文件。"""
    try:
        return Path(md_path).read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return None


def _scan_text(text, rel_path, used):
    """单份文档的三类规则引用：OS-*、OS-STD-NNN、agentrt。"""
    _scan_os(text, rel_path, used)
    _scan_std(text, rel_path, used)
    _scan_rt(text, rel_path, used)


def _scan_os(text, rel_path, used):
    """OS-* 规则引用，去掉句末标点后登记。"""
    for match in OS_PATTERN.finditer(text):
        _mark(used, match.group().rstrip('.,;:)]}'), rel_path)


def _scan_std(text, rel_path, used):
    """OS-STD-NNN 规则引用，原样登记。"""
    for match in OS_STD_PLAIN_PATTERN.finditer(text):
        _mark(used, match.group(), rel_path)


def _scan_rt(text, rel_path, used):
    """agentrt 规则引用；跳过前一个字符为 '-' 的后缀误匹配。"""
    for match in AGENTRT_PATTERN.finditer(text):
        if match.start() >= 1 and text[match.start() - 1] == '-':
            continue
        _mark(used, match.group().rstrip('.,;:)]}'), rel_path)


def scan_documents(docs_root, registered):
    """Scan all .md files for rule ID usage."""
    used = {}  # id -> [file_paths]

    # Files to skip (the SSoT registry itself, YAML files, etc.)
    skip_files = {'09-ssot-registry.md', 'ssot-registry.yaml'}

    for md_path in md_files(docs_root):
        if os.path.basename(md_path) in skip_files:
            continue

        text = _text_of(md_path)
        if text is None:
            continue

        _scan_text(text, os.path.relpath(md_path, docs_root), used)

    return used


def _issues(registered, used):
    """逐条比对使用点与注册表：未注册记 error，已废弃记 warning。"""
    errors = []
    warnings = []
    for rid in sorted(used):
        files = used[rid]
        if rid not in registered:
            errors.append(
                f"ERROR: '{rid}' used in {len(files)} file(s) "
                f"(first: {files[0]}) but NOT registered in SSoT"
            )
        elif registered[rid].get('status') == 'deprecated':
            warnings.append(
                f"WARN: deprecated '{rid}' still referenced in "
                f"{len(files)} file(s) (first: {files[0]})"
            )
    return errors, warnings


def _summary(registered, used, errors, warnings):
    """表头与计数：先给全局规模，再看问题清单。"""
    print("=" * 72)
    print("SSoT Validation Report")
    print("=" * 72)
    print(f"  Registered IDs:  {len(registered)}")
    print(f"  Used IDs:        {len(used)}")
    print(f"  Errors:          {len(errors)}")
    print(f"  Warnings:        {len(warnings)}")
    print("-" * 72)


def _print_rows(title, items, limit=None):
    """打印一组问题；limit 只裁剪展示条数，总数仍照实报出。"""
    if not items:
        return
    print(f"\n{title}:")
    for item in _shown(items, limit):
        print(f"  {item}")
    _print_more(items, limit)


def _shown(items, limit):
    """limit 存在时只取前 limit 条，否则原样返回。"""
    return items[:limit] if limit else items


def _print_more(items, limit):
    """把被截断的条数一并报出，避免误以为问题就这么多。"""
    if limit and len(items) > limit:
        print(f"  ... and {len(items) - limit} more warnings")


def _verdict(errors, warnings):
    """退出码：未注册即失败；只有废弃告警仍算通过。"""
    if errors:
        print("RESULT: FAIL — unregistered rule IDs found")
        return 1
    if warnings:
        print("RESULT: PASS (with deprecation warnings)")
        return 0
    print("RESULT: PASS — all rule IDs registered")
    return 0


def validate(registered, used):
    """Run validation and report results."""
    errors, warnings = _issues(registered, used)
    _summary(registered, used, errors, warnings)
    _print_rows("ERRORS (unregistered rule IDs)", errors)
    _print_rows("WARNINGS (deprecated rule IDs still in use)", warnings, 20)
    print("-" * 72)
    return _verdict(errors, warnings)


def usage(stream=sys.stdout):
    print(f"Usage: {os.path.basename(sys.argv[0])} <docs_root> <ssot_yaml>",
          file=stream)
    print("  docs_root  — root directory of documentation", file=stream)
    print("  ssot_yaml  — path to ssot-registry.yaml", file=stream)


def _require_dir(path, label):
    """目录必须存在，否则报错退出。"""
    if os.path.isdir(path):
        return
    print(f"ERROR: {label} '{path}' is not a directory")
    sys.exit(2)


def _require_file(path, label):
    """文件必须存在，否则报错退出。"""
    if os.path.isfile(path):
        return
    print(f"ERROR: {label} '{path}' is not a file")
    sys.exit(2)


def main():
    if sys.argv[1:] in (["-h"], ["--help"]):
        usage()
        sys.exit(0)

    if len(sys.argv) != 3:
        usage(sys.stderr)
        sys.exit(2)

    docs_root = sys.argv[1]
    yaml_path = sys.argv[2]

    _require_dir(docs_root, "docs_root")
    _require_file(yaml_path, "ssot_yaml")

    print(f"Loading SSoT YAML: {yaml_path}")
    registered = load_ssot(yaml_path)
    print(f"  → {len(registered)} rule IDs registered")

    print(f"Scanning documents: {docs_root}")
    used = scan_documents(docs_root, registered)
    print(f"  → {len(used)} unique rule IDs found in documents")

    exit_code = validate(registered, used)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
