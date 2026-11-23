#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/tests-linux.sh — 用户态单元测试套件的构建、全跑与形态门禁
#
# 用法:
#   ci/gates/tests-linux.sh
#
# 断言链（缺一不可）：
#   1. UAPI 头先经 headers_install 导出到构建目录：用户态测试只能针对导出
#      后的契约头编译，直接引用源码树的 uapi 会被内核自保护拒绝。
#   2. 出树构建通过：构建目录不得落在源码树内，-Werror 下全部用例可编。
#   3. 全部用例在 CTest 下真实执行并通过。
#   4. 套件形态成立：用例数不少于 4，四个契约用例均已登记，unit 标签存在。
#   5. 缺少 AIRY_HDR_ROOT 时配置必须被拒绝——导出头是硬前提，不能让测试
#      在错误的头根上悄悄通过。
#
# 退出码: 0 通过；1 判定失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

need_cmd cmake
need_cmd ctest
need_cmd make

BUILD="$(airy_build_dir "${ROOT}" AIRY_TESTS_BUILD_ROOT airy-tests-linux-verify)"

rm -rf "${BUILD}"
mkdir -p "${BUILD}"

# ─── 1. 导出 UAPI 头 ─────────────────────────────────────────────────
group "tests-linux: 导出 UAPI 头"
if ! make -C "${ROOT}/kernel" O="${BUILD}/kobj" headers_install \
        INSTALL_HDR_PATH="${BUILD}/hdr" >/dev/null; then
    endgroup
    die "UAPI 头导出失败（内核 O=${BUILD}/kobj）"
fi
endgroup
say "UAPI 头已导出到构建目录"

# ─── 2. 出树构建 ─────────────────────────────────────────────────────
group "tests-linux: 出树构建"
if ! cmake -S "${ROOT}/tests-linux" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=Release \
        -DAIRY_HDR_ROOT="${BUILD}/hdr/include" >/dev/null; then
    endgroup
    die "tests-linux 配置失败（构建目录 ${BUILD}）"
fi
cmake --build "${BUILD}" --parallel 2>&1 | tail -n 30
rc=${PIPESTATUS[0]}
endgroup
[ "${rc}" -eq 0 ] || die "tests-linux 构建失败（构建目录 ${BUILD}）"
say "tests-linux 构建通过"

# ─── 3. 全量执行 ─────────────────────────────────────────────────────
group "tests-linux: 用例"
ctest --test-dir "${BUILD}" --output-on-failure || {
    endgroup
    die "单元用例未通过"
}
endgroup
say "单元用例全部通过"

# ─── 4. 套件形态 ─────────────────────────────────────────────────────
group "tests-linux: 套件形态"
LIST="$(ctest --test-dir "${BUILD}" -N)"
TOTAL="$(printf '%s\n' "${LIST}" | sed -n 's/^Total Tests: //p')"
[ "${TOTAL:-0}" -ge 4 ] || die "用例数 ${TOTAL:-0} 少于 4"

for name in test_ipc_magic test_log_ring_uapi test_superv_uapi \
            test_error_codes; do
    printf '%s\n' "${LIST}" |
        grep -Eq "Test +#[0-9]+: ${name}$" || die "缺少用例 ${name}"
done

ctest --test-dir "${BUILD}" --print-labels |
    grep -Eq '^[[:space:]]*unit$' || die "用例未登记 unit 标签"
endgroup
say "用例数 ${TOTAL}，契约用例与标签齐全"

# ─── 5. 拒绝路径 ─────────────────────────────────────────────────────
group "tests-linux: 拒绝路径"
if cmake -S "${ROOT}/tests-linux" -B "${BUILD}-nohdr" \
        -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1; then
    die "缺少 AIRY_HDR_ROOT 时配置未被拒绝"
fi
endgroup
say "缺少导出头时配置被拒绝"

[ ! -e "${ROOT}/tests-linux/CMakeCache.txt" ] ||
    die "源码树内出现构建残留: tests-linux/CMakeCache.txt"

say "tests-linux 门禁通过"
