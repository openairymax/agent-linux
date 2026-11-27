#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_station_ops.sh — station-ops 门禁回归夹具
#
# 钉住的判据：
#   负向：缺模块/测试/出厂配置、入口不可执行、源文件缺 SPDX、函数名超 15 字节、
#         spec 漏入口源、流水线未调用门禁或缺文件、预算/README 未登记、镜像缺
#         系统包、包内残留桩标记或本机路径，均判 1。
#   正向：真实工作站包与接线自洽时判 0，八面板、渲染帧与权限审计按
#         panels=8 ok、frame=30x100/4x8 ok、denied/ok 收敛。
#
# 用法: bash ci/tests/test_station_ops.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

seed()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/ops" "${FX}/system/tests/ops" "${FX}/system/services" \
        "${FX}/distro/specs" "${FX}/distro/kickstart" "${FX}/ci/baseline" \
        "${FX}/.github/workflows"
    cp "${ROOT}/ci/gates/station-ops.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -a "${ROOT}/system/ops/." "${FX}/system/ops/"
    cp -a "${ROOT}/system/tests/ops/." "${FX}/system/tests/ops/"
    cp -p "${ROOT}/system/bin/airy-station" "${FX}/system/bin/"
    cp -a "${ROOT}/system/services/airylib" \
        "${ROOT}/system/services/daemons" "${FX}/system/services/"
    cp "${ROOT}/system/CMakeLists.txt" "${FX}/system/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
    cp "${ROOT}/distro/kickstart/airymaxos-workstation.ks" \
        "${FX}/distro/kickstart/"
    cp "${ROOT}/.github/workflows/station-verify.yml" "${FX}/.github/workflows/"
    cp "${ROOT}/.github/workflows/README.md" "${FX}/.github/workflows/"
    cp "${ROOT}/ci/baseline/workflow-budgets.txt" "${FX}/ci/baseline/"
}

run() { bash "${FX}/ci/gates/station-ops.sh" 2>&1; }

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"
    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${needle}" ] && ! printf '%s' "${out}" | grep -qF -- "${needle}"; then
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

# 正向：布局、八面板、命令行、权限审计、配置与接线全绿
seed
out="$(run)"; rc=$?
check "真实树判 0" 0 "${rc}" "${out}" "station-ops 门禁通过"
printf '%s' "${out}" | grep -qF 'panels=8 ok' &&
    ok "八面板就绪" || bad "八面板未跑"
printf '%s' "${out}" | grep -qF 'frame=30x100/4x8 ok' &&
    ok "渲染帧尺寸收敛" || bad "渲染帧未跑"
printf '%s' "${out}" | grep -qF 'default.conf=ok' &&
    ok "出厂配置与默认一致" || bad "出厂配置未跑"

# 负向：缺模块 / 缺测试
seed
rm -f "${FX}/system/ops/devstation/perm.py"
out="$(run)"; rc=$?
check "缺模块判负" 1 "${rc}" "${out}" "缺少模块 perm.py"
seed
rm -f "${FX}/system/tests/ops/test_station_perm.py"
out="$(run)"; rc=$?
check "缺测试判负" 1 "${rc}" "${out}" "缺少测试 test_station_perm.py"

# 负向：入口不可执行
seed
chmod -x "${FX}/system/bin/airy-station"
out="$(run)"; rc=$?
check "入口不可执行判负" 1 "${rc}" "${out}" "入口不可执行: bin/airy-station"

# 负向：源文件缺 SPDX
seed
sed -i '1d' "${FX}/system/ops/devstation/model.py"
out="$(run)"; rc=$?
check "缺 SPDX 判负" 1 "${rc}" "${out}" "缺少 SPDX 标识"

# 负向：函数名超过 15 字节
seed
printf 'def aaaaaaaaaaaaaaaa(x):\n    return x\n' \
    >>"${FX}/system/ops/devstation/config.py"
out="$(run)"; rc=$?
check "超长函数名判负" 1 "${rc}" "${out}" "函数名超过 15 字节"

# 负向：缺出厂配置
seed
rm -f "${FX}/system/ops/devstation/default.conf"
out="$(run)"; rc=$?
check "缺出厂配置判负" 1 "${rc}" "${out}" "缺少出厂配置 default.conf"

# 负向：spec 漏登记入口源
seed
sed -i '/^Source105:/d' "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "spec 漏入口源判负" 1 "${rc}" "${out}" "spec 未登记入口源"

# 负向：流水线未调用门禁
seed
sed -i '/ci\/ci\.sh station-ops/d' "${FX}/.github/workflows/station-verify.yml"
out="$(run)"; rc=$?
check "流水线未调用判负" 1 "${rc}" "${out}" "流水线未调用门禁"

# 负向：缺流水线文件
seed
rm -f "${FX}/.github/workflows/station-verify.yml"
out="$(run)"; rc=$?
check "缺流水线判负" 1 "${rc}" "${out}" "缺少流水线 station-verify.yml"

# 负向：预算未登记
seed
sed -i '/station-verify\.yml/d' "${FX}/ci/baseline/workflow-budgets.txt"
out="$(run)"; rc=$?
check "预算未登记判负" 1 "${rc}" "${out}" "预算未登记 station-verify.yml"

# 负向：README 未同步
seed
sed -i '/station-verify/d' "${FX}/.github/workflows/README.md"
out="$(run)"; rc=$?
check "README 未同步判负" 1 "${rc}" "${out}" "README 未同步 station-verify"

# 负向：镜像缺系统包
seed
sed -i '/^airymaxos-system$/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run)"; rc=$?
check "镜像缺系统包判负" 1 "${rc}" "${out}" "工作站镜像未包含系统包"

# 负向：残留桩标记 / 本机路径
seed
printf '\n# TODO\n' >>"${FX}/system/ops/devstation/render.py"
out="$(run)"; rc=$?
check "残留桩标记判负" 1 "${rc}" "${out}" "残留桩标记"
seed
printf '\n# /home/dev\n' >>"${FX}/system/ops/devstation/tui.py"
out="$(run)"; rc=$?
check "本机路径判负" 1 "${rc}" "${out}" "残留本机绝对路径"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
