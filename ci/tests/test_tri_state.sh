#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_tri_state.sh — ci/gates/tri-state.sh 的分支回归
#
# 三态协议的审计判据本身也要有判据：真实树全绿只能说明今天没有漂移。
# 这里用最小夹具逐条造出四类漂移——缺退出码段、越界退出码、EINVAL 分叉、
# 语法错误——每一种都必须判负；参数与环境问题按 5 退，帮助按 0 退。
#
# 用法: bash ci/tests/test_tri_state.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/tri-state.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0
G="${tmp}/t/ci/gates"

gate()
{
    {
        printf '#!/usr/bin/env bash\n'
        printf '# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0\n'
        printf '# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.\n'
        printf '# %s\n' "$2"
        printf 'set -uo pipefail\n%s\n' "$3"
    } >"${G}/$1"
}

reset()
{
    rm -rf "${tmp}/t"
    mkdir -p "${G}"
    gate ok.sh "退出码: 0 通过；1 判负" 'exit 0'
    gate na.sh "退出码: 0 通过；3 不适用" 'exit 3'
    gate arg.sh "退出码: 0 通过；1 判负；5 参数不满足" \
        $'readonly EINVAL=5\n[ -n "${1:-}" ] || exit "${EINVAL}"'
}

try()
{
    out="$(bash "${GATE}" "$@" 2>&1)"
    rc=$?
}

expect()
{
    if [ "${rc}" -ne "$2" ] || ! printf '%s' "${out}" | grep -qF "$3"; then
        printf 'FAIL %s（rc=%s，期望 %s，判据「%s」）\n' "$1" "${rc}" "$2" "$3"
        printf '%s\n' "${out}" >&2
        fails=$((fails + 1))
        return 0
    fi
    printf 'ok   %s\n' "$1"
}

reset
try --root "${tmp}/t"
expect "合规夹具全绿" 0 "不合规 0 条"

reset
sed -i '/^# 退出码:/d' "${G}/ok.sh"
try --root "${tmp}/t"
expect "缺退出码段判负" 1 "缺少"

reset
sed -i 's/^exit 0$/exit 7/' "${G}/ok.sh"
try --root "${tmp}/t"
expect "越界退出码判负" 1 "不在协议内"

reset
sed -i 's/readonly EINVAL=5/readonly EINVAL=4/' "${G}/arg.sh"
try --root "${tmp}/t"
expect "EINVAL 分叉判负" 1 "EINVAL=4"

reset
printf 'if then\n' >>"${G}/ok.sh"
try --root "${tmp}/t"
expect "语法错误判负" 1 "语法错误"

reset
try --bogus
expect "未知参数退 5" 5 "未知参数"

try --root
expect "缺 --root 参数退 5" 5 "需要目录"

try --help
expect "帮助含用法行" 0 "用法:"

rm -rf "${tmp}/t"
try --root "${tmp}/t"
expect "门禁目录缺失退 5" 5 "门禁目录不存在"

mkdir -p "${G}"
try --root "${tmp}/t"
expect "空门禁目录退 5" 5 "未找到门禁"

if [ "${fails}" -ne 0 ]; then
    printf 'test_tri_state: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_tri_state: 全部通过\n'
