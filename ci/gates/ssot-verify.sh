#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/ssot-verify.sh — SSoT 注册表与本仓文档的自洽门禁
#
# 用法:
#   ci/gates/ssot-verify.sh --kind syntax|links
#
#   syntax  ssot-registry.yaml 可解析，且登记的规则 ID 总数与解析结果一致
#           （OS-IRON-015），随后交给 tools/validate-ssot.py 逐条核对。
#   links   仅扫描 git 跟踪的本仓 .md，校验相对链接是否落到真实文件；
#           跨仓（../）与外链（http/mailto）不在此判定。
#
# 两段判定原先是 ssot-validate.yml 里的内联 heredoc，只有改动 .md 时才会
# 在云端跑到，本地无从复现。抽成 gate 后可在提交前直接执行。
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/ssot-verify.sh --kind syntax|links
EOF
}

KIND=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --kind) KIND="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

cd "${ROOT}" || die "无法进入仓库根: ${ROOT}"

case "${KIND}" in
    syntax)
        [ -f ssot-registry.yaml ] || die "ssot-registry.yaml 不在仓库根"
        # 本地 python3 可能没有 pip（发行版拆分了 ensurepip），但常常已装
        # PyYAML；先探测 import，缺了才装，避免本地重放被环境细节卡住。
        python3 -c 'import yaml' 2>/dev/null ||
            python3 -m pip install -q pyyaml ||
            die "PyYAML 不可用，且安装失败"
        python3 - <<'PY' || die "ssot-registry.yaml 语法或规则计数校验失败"
import re
import sys

import yaml

with open('ssot-registry.yaml') as f:
    data = yaml.safe_load(f)

total = 0
for key in ('agentrt_linux_rules', 'agentrt_rules'):
    for g in data.get(key, []) or []:
        total += len(g.get('entries', []) or [])
        for r in g.get('ranges', []) or []:
            s = int(re.search(r'(\d+)$', r['start']).group(1))
            e = int(re.search(r'(\d+)$', r['end']).group(1))
            total += (e - s + 1)
        total += len(g.get('individual_entries', []) or [])
print(f'YAML valid. Registered rule IDs: {total}')
PY
        python3 tools/validate-ssot.py . ssot-registry.yaml ||
            die "规则 ID 与注册表不一致（OS-IRON-015）"
        say "SSOT 注册表自洽"
        ;;
    links)
        python3 - <<'PY' || die "存在失效的本仓相对链接"
import os
import re
import subprocess
import sys

listed = subprocess.run(
    ['git', 'ls-files', '-z', '--', '*.md'],
    capture_output=True, check=True,
).stdout.decode('utf-8', errors='replace')
broken = 0
for path in [p for p in listed.split('\0') if p]:
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for i, line in enumerate(fh, 1):
            for m in re.finditer(r'\]\(([^)]+\.md[^)]*)\)', line):
                link = m.group(1).split('#')[0].strip()
                if (not link
                        or link.startswith(('http://', 'https://', 'mailto:'))
                        or link.startswith('../')):
                    continue
                target = os.path.normpath(
                    os.path.join(os.path.dirname(path), link))
                if not os.path.exists(target):
                    print(f'BROKEN LINK: {path}:{i} -> {link}')
                    broken += 1
if broken:
    print(f'FAIL: {broken} broken relative markdown link(s)')
    sys.exit(1)
print('OK: all repo-owned relative markdown links resolve')
PY
        say "本仓相对链接一致"
        ;;
    *)
        usage >&2
        exit "${EINVAL}"
        ;;
esac
