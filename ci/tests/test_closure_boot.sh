#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_closure_boot.sh — 引导链必须进载荷事务（回归）
#
# 装机阶段 anaconda 通过 GRUB2.packages 隐式请求引导包，comps 刻意不列它们
# 的二进制名（x86_64 与 aarch64 不同名，写进组里必有一侧落空），交由
# cl_boot 按架构补齐。0.0.4 的 UEFI 装机在分区之后停在「缺少软件包
# shim-x64、grub2-efi-x64」的交互确认上，而 ISO 构建全绿：cl_boot 写好了
# 却没人调用，事务里从来没有这组包。
#
# 本回归锁住接线本身：产品模式与播种模式下 cl_args 都必须把引导链并入
# 事务，播种跳过项不得把它摘掉，未登记架构必须显式失败而不是静默少包。
#
# 用法: bash ci/tests/test_closure_boot.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../../distro/scripts/lib/closure.sh
. "${ROOT}/distro/scripts/lib/closure.sh"

fails=0
# shellcheck disable=SC2317  # 供下方断言回调
ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

has()
{
    case " ${CL_ARGS[*]} " in
    *" $1 "*) return 0 ;;
    esac
    return 1
}

boot_x86()
{
    local p
    for p in grub2-pc grub2-efi-x64 shim-x64 efibootmgr; do
        has "$p" && ok "$1 事务含 ${p}" || bad "$1 事务缺 ${p}"
    done
}

CL_SPECS=(@airymaxos-install)
cl_args x86_64 || bad "产品模式求解参数失败"
boot_x86 产品模式

# 播种模式：自建包尚未产出，跳过项把它们摘掉；引导链不是自建包，必须留下。
# shellcheck disable=SC2034  # 两者都是 cl_args 的入参
CL_SPECS=(@airymaxos-install airymaxos-system)
# shellcheck disable=SC2034  # 同上
CLOSURE_SKIP='airymaxos-*'
cl_args x86_64 || bad "播种模式求解参数失败"
boot_x86 播种模式
has airymaxos-system && bad "播种模式未摘掉自建包" || ok "播种模式摘掉自建包"

if cl_args loongarch64 2>/dev/null; then
    bad "未登记架构 loongarch64 未失败"
else
    ok "未登记架构 loongarch64 显式失败"
fi

[ "${fails}" -eq 0 ] || { printf '%d 项断言失败\n' "${fails}"; exit 1; }
printf '全部通过\n'
