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
# 后半段锁住载荷核对的口径：请求名可以是虚拟名（shim-x64 由 shim 提供），
# 判定要同时接受文件名与包内 Provides，真缺的项仍须逐个列出。
#
# 用法: bash ci/tests/test_closure_boot.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin"
# 伪 rpm 只回答 Provides 查询，Provides 取自包体的 provides: 行；宿主有没有
# 装 rpm、装了什么包都不影响结论。
cat >"${tmp}/bin/rpm" <<'SH'
#!/bin/sh
for a in "$@"; do
    [ -f "$a" ] || continue
    sed -n 's/^provides: //p' "$a"
done
SH
chmod +x "${tmp}/bin/rpm"
# shellcheck disable=SC2034  # 由 closure.sh 在 source 时读取
CLOSURE_RPM="${tmp}/bin/rpm"

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

# 载荷核对：请求名可能是虚拟名。openEuler 的 shim 包提供 shim-x64，dnf 求解
# 时已把请求换成它；按文件名逐字比对会把已满足的请求判成缺包
# （run 41792595223：事务里 shim 已就位，构建却报「缺少引导包 shim-x64」）。
mkdir -p "${tmp}/pkg"
printf 'provides: shim\nprovides: shim-x64\n' \
    >"${tmp}/pkg/shim-15.7-27.oe2403sp3.x86_64.rpm"
printf 'rpm grub2-efi-x64\n' >"${tmp}/pkg/grub2-efi-x64-2.12-56.x86_64.rpm"

out="$(cl_miss "${tmp}/pkg" shim-x64 grub2-efi-x64)"
[ -z "$out" ] && ok "虚拟名与同名包都被认可" || bad "误报缺包: ${out}"
out="$(cl_miss "${tmp}/pkg" shim-x64 efibootmgr)"
[ "$out" = "efibootmgr" ] && ok "真缺的请求名被列出" \
    || bad "缺包判定异常: ${out}"
out="$(cl_miss "${tmp}/pkg" efibootmgr grub2-pc | tr '\n' ' ')"
[ "$out" = "efibootmgr grub2-pc " ] && ok "多个缺项逐个列出" \
    || bad "多个缺项判定异常: ${out}"

[ "${fails}" -eq 0 ] || { printf '%d 项断言失败\n' "${fails}"; exit 1; }
printf '全部通过\n'
