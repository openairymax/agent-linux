#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/kernel-debugfs.sh — 调试面（/sys/kernel/debug/airy）契约门禁
#
# 用法:
#   ci/gates/kernel-debugfs.sh [--src DIR]
#
# 调试面没有稳定 ABI，也因此最容易在重构中失守：注册点提前到 core_initcall
# 会在 debugfs 自身初始化之前返回 -ENODEV；节点改成可写就把调试接口变成攻击
# 面；顺手打印防伪随机标签则直接泄露徽章的秘密。这些在编译期都不报，只能在
# 实机上才发现，因此把契约固化成静态门禁。
#
# 判据：
#   * 由 fs_initcall 注册：debugfs 自身是 core_initcall，而内核目录先于文件
#     系统目录链接，同一阶段注册必然拿不到 debugfs。
#   * 依赖 /sys/airy 的就绪标记，失败只告警；调试面缺席不撤回运行时就绪。
#   * 节点一律 0444 只读，不使用 debugfs_create_u32/blob 等可写投影接口。
#   * 只导出三个有真实数据源的节点：能力槽明细、最近裁决、日志环原始字节。
#   * 防伪随机标签（randtag）不出现在任何非测试源文件中。
#   * Kbuild 条件对象与四架构 defconfig 的 DEBUG_FS/TRACING 基线齐备。
#   * 源码文件带 SPDX 与版权行。
#
# 退出码: 0 通过；1 契约不成立；5 参数或目录不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SRC="${ROOT}/kernel"

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-debugfs.sh [--src DIR]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    *)
        err "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

OBS="${SRC}/kernel/obs"
DBG="${OBS}/airy_obs_debugfs.c"
FAILS=0

bad() { err "$*"; FAILS=$((FAILS + 1)); }

[ -d "${SRC}" ] || { err "内核源码目录不存在: ${SRC}"; exit "${EINVAL}"; }
[ -f "${DBG}" ] || { err "缺少调试面源文件: ${DBG}"; exit "${EINVAL}"; }

# 注册时序 -----------------------------------------------------------------
chk_place()
{
    group "注册时序"
    grep -qE '^fs_initcall\(airy_dbg_start\);' "${DBG}" ||
        bad "调试面未在 fs_initcall 注册（debugfs 尚未就绪）"
    grep -q 'airy_obs_ready' "${DBG}" ||
        bad "调试面未依赖 /sys/airy 的就绪标记"
    if grep -q 'airy_probe_exit' "${DBG}"; then
        bad "调试面失败不应撤回运行时就绪标记"
    fi
    grep -q 'pr_warn(' "${DBG}" ||
        bad "调试面创建失败未告警"
    endgroup
}

# 只读契约 -----------------------------------------------------------------
chk_readonly()
{
    local calls ro

    group "只读契约"
    calls=$(grep -c 'debugfs_create_file(' "${DBG}")
    ro=$(grep -cE 'debugfs_create_file\([^,]+,[[:space:]]*0444,' "${DBG}")
    [ "${calls}" -gt 0 ] || bad "调试面未创建任何文件"
    [ "${calls}" -eq "${ro}" ] || bad "存在非 0444 的调试文件"

    if grep -qE 'debugfs_create_(u8|u16|u32|u64|bool|blob|xarray|atomic)' \
        "${DBG}"; then
        bad "使用可写投影接口创建调试节点"
    fi
    if grep -qE '\.[[:space:]]*write[[:space:]]*=[[:space:]]*' "${DBG}"; then
        bad "调试文件出现写回调"
    fi
    endgroup
}

# 节点面 -------------------------------------------------------------------
chk_nodes()
{
    local s name

    group "节点面"
    for name in cap_table last_decision ring_buffer_dump; do
        grep -q "mk_node(dir, \"${name}\"" "${DBG}" ||
            bad "缺少调试节点 ${name}"
    done
    for s in sec superv log; do
        grep -q "dbg_group(\"${s}\"" "${DBG}" ||
            bad "缺少调试分组 ${s}"
    done
    endgroup
}

# 机密边界 -----------------------------------------------------------------
chk_secret()
{
    local f

    group "机密边界"
    for f in "${OBS}"/*.c; do
        [ -f "${f}" ] || continue
        case "${f}" in
        *_test.c) continue ;;
        esac
        if grep -q 'randtag' "${f}"; then
            bad "$(basename "${f}"): 观测/调试面引用了防伪随机标签"
        fi
    done
    grep -q 'airy_obs_cap_fmt' "${OBS}/airy_obs_sec.c" ||
        bad "缺少能力槽行格式化实现"
    endgroup
}

# 构建与配置 ---------------------------------------------------------------
chk_build()
{
    local cfg arch

    group "构建与配置"
    grep -qE '^airy_obs-\$\(CONFIG_DEBUG_FS\)[[:space:]]*\+=[[:space:]]*airy_obs_debugfs\.o' \
        "${OBS}/Kbuild" ||
        bad "Kbuild 未按 CONFIG_DEBUG_FS 引入调试面对象"
    for arch in x86 arm64 riscv loongarch; do
        cfg="${SRC}/arch/${arch}/configs/airy_defconfig"
        if [ ! -f "${cfg}" ]; then
            bad "缺少 ${arch} 的 airy_defconfig"
            continue
        fi
        for name in DEBUG_FS TRACING EVENT_TRACING; do
            grep -qx "CONFIG_${name}=y" "${cfg}" ||
                bad "${arch}/airy_defconfig 未开启 CONFIG_${name}"
        done
    done
    endgroup
}

# 文件头 -------------------------------------------------------------------
chk_headers()
{
    group "文件头"
    sed -n '1p' "${DBG}" |
        grep -q 'SPDX-License-Identifier: GPL-2.0-only' ||
        bad "调试面源文件首行缺少 SPDX"
    sed -n '1,5p' "${DBG}" | grep -q 'Copyright' ||
        bad "调试面源文件缺少版权行"
    endgroup
}

chk_place
chk_readonly
chk_nodes
chk_secret
chk_build
chk_headers

if [ "${FAILS}" -ne 0 ]; then
    err "调试面契约门禁失败 ${FAILS} 项"
    exit 1
fi

say "调试面契约门禁通过"
exit 0
