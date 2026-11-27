#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_system_inventory.sh — system-inventory 门禁回归夹具
#
# 钉住的判据：
#   负向：包装脚本不转交 system、清点模块交付清单路径漂移、kickstart 未落盘
#         交付集合或未取 rpm 包名、冒烟脚本未登记/未取 T23，均判 1；未知参数
#         判 1。
#   正向：真实模块与脚本自洽时判 0，行为面按 0/1/3 与 ok/failed/drift/unknown
#         四个 state 收敛。
#
# 用法: bash ci/tests/test_system_inventory.sh
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
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/services" \
        "${FX}/distro/desktop" "${FX}/distro/ci" "${FX}/distro/kickstart"
    cp "${ROOT}/ci/gates/system-inventory.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -a "${ROOT}/system/services/airymax_observe" "${FX}/system/services/"
    cp -a "${ROOT}/system/services/airymax_power" "${FX}/system/services/"
    cp "${ROOT}/distro/desktop/airy_observe.sh" "${FX}/distro/desktop/"
    cp "${ROOT}/distro/ci/run-smoke.sh" "${FX}/distro/ci/"
    cp "${ROOT}/distro/kickstart/airymaxos-minimal.ks" \
        "${FX}/distro/kickstart/"
    cp "${ROOT}/distro/kickstart/airymaxos-workstation.ks" \
        "${FX}/distro/kickstart/"
}

run() { bash "${FX}/ci/gates/system-inventory.sh" "$@" 2>&1; }

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

# 正向：真实树判 0，行为面 0/1/3 全跑通
seed
out="$(run)"; rc=$?
check "真实树判 0" 0 "${rc}" "${out}" "== system-inventory passed =="
printf '%s' "${out}" | grep -qF '三面干净判 0' &&
    ok "行为面干净样本判 0" || bad "行为面干净样本未跑"
printf '%s' "${out}" | grep -qF '证据缺失判 3' &&
    ok "行为面缺证样本判 3" || bad "行为面缺证样本未跑"

# 负向：包装脚本不转交 system
seed
sed -i 's/\[ "\$1" = system \]/[ "$1" = inventory ]/' \
    "${FX}/distro/desktop/airy_observe.sh"
out="$(run)"; rc=$?
check "包装脚本不转交判负" 1 "${rc}" "${out}" "包装脚本未转交 system 子命令"

# 负向：包装脚本吞掉参数
seed
sed -i 's/exec python3 -B -m airymax_observe "\$@"/exec python3 -B -m airymax_observe/' \
    "${FX}/distro/desktop/airy_observe.sh"
out="$(run)"; rc=$?
check "包装脚本吞参判负" 1 "${rc}" "${out}" "包装脚本未把参数交给"

# 负向：交付清单路径漂移
seed
sed -i 's#^MANIFEST = "/etc/airymaxos-packages"$#MANIFEST = "/etc/packages"#' \
    "${FX}/system/services/airymax_observe/sys.py"
out="$(run)"; rc=$?
check "清单路径漂移判负" 1 "${rc}" "${out}" "交付清单路径不是"

# 负向：缺清点模块
seed
rm -f "${FX}/system/services/airymax_observe/sys.py"
out="$(run)"; rc=$?
check "缺清点模块判负" 1 "${rc}" "${out}" "缺少清点模块"

# 负向：kickstart 未落盘交付集合
seed
sed -i 's#/etc/airymaxos-packages#/etc/packages#g' \
    "${FX}/distro/kickstart/airymaxos-minimal.ks"
out="$(run)"; rc=$?
check "kickstart 未落盘判负" 1 "${rc}" "${out}" "未写交付包集合"

# 负向：kickstart 未从 rpm 取包名
seed
sed -i '/rpm -qa/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run)"; rc=$?
check "kickstart 未取包名判负" 1 "${rc}" "${out}" "未从 rpm 数据库取包名"

# 负向：冒烟脚本未登记 T23
seed
sed -i '/^probe 23 /d' "${FX}/distro/ci/run-smoke.sh"
out="$(run)"; rc=$?
check "冒烟未登记 T23 判负" 1 "${rc}" "${out}" "冒烟脚本未登记 T23"

# 负向：T23 未取同一判定实现
seed
sed -i 's/airy-observe system --manifest/airy-observe units --manifest/' \
    "${FX}/distro/ci/run-smoke.sh"
out="$(run)"; rc=$?
check "T23 取错判定判负" 1 "${rc}" "${out}" "T23 未取 airy-observe system 的判定"

# 负向：未知参数；正向：帮助
seed
out="$(run --bogus)"; rc=$?
check "未知参数判 1" 1 "${rc}" "${out}" "未知参数: --bogus"
out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
