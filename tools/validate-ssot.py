#!/usr/bin/env python3
"""
validate-ssot.py — SSoT Rule ID Consistency Validator

Validates that every OS-*-NNN (agentrt-linux) and *-NNN (agentrt) rule ID
referenced in documentation is registered in the SSoT YAML registry.

Usage:
    python3 tools/validate-ssot.py <docs_root> <ssot_yaml>

Exit codes:
    0 — all rule IDs are registered (or only deprecated warnings)
    1 — one or more unregistered rule IDs found
    2 — YAML parsing or file error

Authority: docs/AirymaxOS/50-engineering-standards/09-ssot-registry.md
OS-IRON-015: All rule IDs must be registered; deprecated IDs are retained
             but marked as deprecated.
"""

import re
import sys
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(2)


# ─── Rule ID Patterns ──────────────────────────────────────────────────────

# agentrt-linux rules: OS-<PREFIX>-<SUBDOMAIN>-NNN or OS-<PREFIX>-NNN
OS_PATTERN = re.compile(
    r'\bOS-'
    r'(?:'
    r'IRON|KER|STD-CODE|STD-FMT|STD-STY|STD-GOV|STD-RUST|STD-TOOL|'
    r'STD-PROD|STD-DOC|STD-SEC|STD-SPDX|STD-TEST|'
    r'BAN|ACC|ABI|SEC|ARCH|IFACE|TEST|'
    r'OPS|IPC|CHK-DOC|CHK-CODE|CHK-IRON|DEV|BUILD|DRV|OBS|MM|TST|FMT'
    r')'
    r'-?\d+(?:[~–\-]\d+)?'
    r'(?:-SP\d+|SP\d+|OS\d+)?'
    r'\b'
)

# agentrt rules: <PREFIX>-NNN (no OS- prefix)
AGENTRT_PATTERN = re.compile(
    r'\b(?:'
    r'IRON|BAN|STD|ACC|FOUND|SPLIT|PROD|ARC|LC|PRT|LOG|'
    r'PATH-BAN|L|CROSS|REQ|W|SP'
    r')-\d+(?:[~–\-]\d+)?'
    r'(?:-SP\d+|SP\d+|OS\d+)?'
    r'\b'
)

# OS-STD-030~057 (Rust rules use plain OS-STD-NNN format)
OS_STD_PLAIN_PATTERN = re.compile(r'\bOS-STD-\d{2,3}\b')


def load_ssot(yaml_path):
    """Load the SSoT YAML registry and expand ranges into a full ID set."""
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    registered = {}  # id -> {status, desc, section}

    def extract_number(rid):
        """Extract the numeric part from a rule ID."""
        m = re.search(r'(\d+)$', rid)
        return int(m.group(1)) if m else None

    def get_prefix(rid):
        """Extract the prefix from a rule ID."""
        m = re.match(r'(.+?)-\d+$', rid)
        return m.group(1) if m else rid

    def expand_range(start, end):
        """Expand a range like OS-KER-001 ~ OS-KER-228 into individual IDs."""
        start_num = extract_number(start)
        end_num = extract_number(end)
        prefix = get_prefix(start)
        if start_num is None or end_num is None:
            return []
        # Determine zero-padding from the start string
        m = re.search(r'(\d+)$', start)
        pad = len(m.group(1)) if m else 3
        return [
            f"{prefix}-{str(n).zfill(pad)}"
            for n in range(start_num, end_num + 1)
        ]

    # Process agentrt-linux (OS-*) rules
    for group in data.get('agentrt_linux_rules', []):
        prefix = group.get('prefix', '')
        entries = group.get('entries', [])
        ranges = group.get('ranges', [])

        # Full entries
        for entry in entries:
            eid = entry['id']
            registered[eid] = {
                'status': entry.get('status', 'active'),
                'desc': entry.get('desc', ''),
                'section': entry.get('section', ''),
            }

        # Range entries
        for r in ranges:
            for eid in expand_range(r['start'], r['end']):
                registered[eid] = {
                    'status': 'active',
                    'desc': f"Range entry: {r['start']} ~ {r['end']}",
                    'section': r.get('section', ''),
                }

        # Deprecated entries (override range entries)
        for dep_id in group.get('deprecated', []):
            if dep_id in registered:
                registered[dep_id]['status'] = 'deprecated'
            else:
                registered[dep_id] = {
                    'status': 'deprecated',
                    'desc': 'Deprecated (not in active range)',
                    'section': '',
                }

        # Individual non-contiguous entries
        for entry in group.get('individual_entries', []):
            eid = entry['id']
            registered[eid] = {
                'status': entry.get('status', 'active'),
                'desc': entry.get('desc', ''),
                'section': entry.get('section', ''),
            }

    # Process agentrt (non-OS) rules
    for group in data.get('agentrt_rules', []):
        entries = group.get('entries', [])
        ranges = group.get('ranges', [])

        for entry in entries:
            eid = entry['id']
            registered[eid] = {
                'status': entry.get('status', 'active'),
                'desc': entry.get('desc', ''),
                'section': entry.get('section', ''),
            }

        for r in ranges:
            for eid in expand_range(r['start'], r['end']):
                registered[eid] = {
                    'status': 'active',
                    'desc': f"Range entry: {r['start']} ~ {r['end']}",
                    'section': r.get('section', ''),
                }

        for dep_id in group.get('deprecated', []):
            if dep_id in registered:
                registered[dep_id]['status'] = 'deprecated'
            else:
                registered[dep_id] = {
                    'status': 'deprecated',
                    'desc': 'Deprecated',
                    'section': '',
                }

        for entry in group.get('individual_entries', []):
            eid = entry['id']
            registered[eid] = {
                'status': entry.get('status', 'active'),
                'desc': entry.get('desc', ''),
                'section': entry.get('section', ''),
            }

    return registered


def scan_documents(docs_root, registered):
    """Scan all .md files for rule ID usage."""
    used = {}  # id -> [file_paths]

    # Files to skip (the SSoT registry itself, YAML files, etc.)
    skip_files = {'09-ssot-registry.md', 'ssot-registry.yaml'}

    for md_path in Path(docs_root).rglob('*.md'):
        if md_path.name in skip_files:
            continue

        try:
            text = md_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        rel_path = str(md_path.relative_to(docs_root))

        # Find OS-* rule IDs
        for match in OS_PATTERN.finditer(text):
            rid = match.group().rstrip('.,;:)]}')
            used.setdefault(rid, []).append(rel_path)

        # Find OS-STD-NNN plain format
        for match in OS_STD_PLAIN_PATTERN.finditer(text):
            rid = match.group()
            used.setdefault(rid, []).append(rel_path)

        # Find agentrt rule IDs (but not inside OS-* IDs)
        # We need to avoid matching OS-IRON-001 as IRON-001
        for match in AGENTRT_PATTERN.finditer(text):
            rid = match.group().rstrip('.,;:)]}')
            # Skip if preceded by OS-
            start = match.start()
            if start >= 3 and text[start-3:start] == 'OS-':
                continue
            used.setdefault(rid, []).append(rel_path)

    return used


def validate(registered, used):
    """Run validation and report results."""
    errors = []
    warnings = []

    # Check for unregistered IDs
    for rid in sorted(used):
        if rid not in registered:
            files = used[rid]
            errors.append(
                f"ERROR: '{rid}' used in {len(files)} file(s) "
                f"(first: {files[0]}) but NOT registered in SSoT"
            )
        elif registered[rid].get('status') == 'deprecated':
            files = used[rid]
            warnings.append(
                f"WARN: deprecated '{rid}' still referenced in "
                f"{len(files)} file(s) (first: {files[0]})"
            )

    # Report
    print("=" * 72)
    print("SSoT Validation Report")
    print("=" * 72)
    print(f"  Registered IDs:  {len(registered)}")
    print(f"  Used IDs:        {len(used)}")
    print(f"  Errors:          {len(errors)}")
    print(f"  Warnings:        {len(warnings)}")
    print("-" * 72)

    if errors:
        print("\nERRORS (unregistered rule IDs):")
        for e in errors:
            print(f"  {e}")

    if warnings:
        print("\nWARNINGS (deprecated rule IDs still in use):")
        for w in warnings[:20]:  # Limit warnings output
            print(f"  {w}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more warnings")

    print("-" * 72)
    if errors:
        print("RESULT: FAIL — unregistered rule IDs found")
        return 1
    elif warnings:
        print("RESULT: PASS (with deprecation warnings)")
        return 0
    else:
        print("RESULT: PASS — all rule IDs registered")
        return 0


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <docs_root> <ssot_yaml>")
        print(f"  docs_root  — root directory of documentation")
        print(f"  ssot_yaml  — path to ssot-registry.yaml")
        sys.exit(2)

    docs_root = sys.argv[1]
    yaml_path = sys.argv[2]

    if not os.path.isdir(docs_root):
        print(f"ERROR: docs_root '{docs_root}' is not a directory")
        sys.exit(2)

    if not os.path.isfile(yaml_path):
        print(f"ERROR: ssot_yaml '{yaml_path}' is not a file")
        sys.exit(2)

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
