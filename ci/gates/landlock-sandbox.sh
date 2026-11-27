#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/landlock-sandbox.sh — Landlock 用户态沙箱（P-19 / CA-076）
# 的库、CLI、策略、测试与打包接线门禁
#
# 用法:
#   ci/gates/landlock-sandbox.sh
#
# 断言链：
#   1. 库、CLI、策略示例、测试与两份 CMakeLists 齐全，源码带 SPDX。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿；内核不支持时运行期
#      用例必须以 `# SKIP landlock unavailable` 记录。
#   3. status 可执行并打印 abi=<n>，出厂策略通过 check。
#   4. 坏策略退出码为 3；不可用内核上运行期用例确实走 SKIP。
#   5. spec 与 workflow 接线成立；security 源码树无构建残留与占位标记。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

LL="${ROOT}/security/landlock"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/landlock/airy-landlock"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "landlock-sandbox: 布局"
for name in ll_api.h ll_sys.c ll_policy.c ll_main.c \
    tests/ll_test.c CMakeLists.txt policies/agent.conf; do
    [ -f "${LL}/${name}" ] || bad "缺少 security/landlock/${name}"
done
[ -f "${ROOT}/security/CMakeLists.txt" ] \
    || bad "缺少 security/CMakeLists.txt"
for src in "${LL}"/ll_api.h "${LL}"/ll_sys.c "${LL}"/ll_policy.c \
    "${LL}"/ll_main.c "${LL}"/tests/ll_test.c; do
    have '^// SPDX-License-Identifier: GPL-2.0-only$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
for src in "${LL}"/CMakeLists.txt "${ROOT}/security/CMakeLists.txt"; do
    have '^# SPDX-License-Identifier: GPL-2.0-only$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
have '^# SPDX-License-Identifier: GPL-2.0-only$' \
    "${LL}/policies/agent.conf" "出厂策略缺少 SPDX"
endgroup

# ─── 2. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "landlock-sandbox: 出树构建与测试"
airy_build_test "${ROOT}/security" "${BUILD}" security && built=1
endgroup

# ─── 3. CLI 行为 ─────────────────────────────────────────────────────
group "landlock-sandbox: CLI"
abi=-1
if [ "${built}" -eq 1 ]; then
    out="$("${BIN}" status 2>&1)" || bad "status 执行失败"
    have '^abi=[0-9]+$' <(printf '%s\n' "${out}") \
        "status 未打印 abi=<n>"
    case "${out}" in
        abi=*) abi="${out#abi=}" ;;
    esac
    case "${abi}" in
        ''|*[!0-9]*) abi=-1 ;;
    esac
    [ "${abi}" -ge 0 ] || bad "status 的 abi 不合法: ${out}"

    rc=0
    out="$("${BIN}" check "${LL}/policies/agent.conf" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "出厂策略 check 退出码 ${rc}，期望 0"
    have '^rules=5 handled=0x[0-9a-f]+$' <(printf '%s\n' "${out}") \
        "出厂策略输出不是 rules=5 handled=0x..."

    printf 'ro relative/path\n' > "${WORK}/bad.conf"
    rc=0
    "${BIN}" check "${WORK}/bad.conf" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "坏策略退出码 ${rc}，期望 3"

    if [ "${abi}" -eq 0 ]; then
        tap="$("${BUILD}/landlock/ll_test" 2>&1)" \
            || bad "ll_test 在不可用内核上失败"
        have 'ok [0-9]+ - .* # SKIP landlock unavailable' \
            <(printf '%s\n' "${tap}") "运行期用例未按需 SKIP"
    fi
fi
endgroup

# ─── 4. 打包与流水线接线 ─────────────────────────────────────────────
group "landlock-sandbox: 安装接线"
airy_security_wire landlock-sandbox landlock landlock-sandbox \
    'security-build/landlock/airy-landlock' \
    '%\{_bindir\}/airy-landlock' \
    '%\{_datadir\}/airymax/landlock/agent\.conf' \
    '^%dir %\{_datadir\}/airymax/landlock$'
endgroup

# ─── 5. 源码树卫生 ───────────────────────────────────────────────────
group "landlock-sandbox: 源码树卫生"
leftover="$(find "${ROOT}/security" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name ll_test -o -name airy-landlock \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for d in "${ROOT}/security-build" "${LL}/build"; do
    [ ! -e "${d}" ] || bad "源码树残留构建目录: ${d}"
done
for src in "${LL}"/*.c "${LL}"/*.h "${LL}"/tests/*.c \
    "${LL}"/policies/agent.conf; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "landlock-sandbox 门禁失败 ${FAILS} 项"
say "landlock-sandbox 门禁通过"
