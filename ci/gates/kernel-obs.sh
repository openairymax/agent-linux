#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/kernel-obs.sh — 观测面（/sys/airy、/proc/airy）契约门禁
#
# 用法:
#   ci/gates/kernel-obs.sh [--src DIR]
#
# 观测面是运维脚本与发布物料的接口，也最容易在重构中悄悄漂移：注册点挪回
# LSM 初始化就会在 kernfs 建立之前失败，属性改用 scnprintf 就会踩 sysfs_emit
# 的页对齐断言，新增的源文件漏进 Kbuild 就会「代码在、节点不在」。这些在
# 编译期都不报，只能等开机后才暴露，因此把契约固化成静态门禁。
#
# 判据：
#   * 顶层目录由 core_initcall 发布（kernfs 已就绪、用户态未起），失败撤回
#     agentrt 就绪标记；LSM 初始化不再碰观测面。
#   * 属性一律经 AIRY_OBS_ATTR 构造（固定 0444、无 store），且同文件内存在
#     同名 show 回调；obs 目录内不出现 scnprintf/snprintf/sprintf。
#   * /proc/airy 走 single_open，读取不消费故障环。
#   * Kbuild 的源文件清单与目录内容一致；三份 defconfig 与 KUnit 配置均已
#     开启 CONFIG_AIRY_OBS。
#   * 目录内每个源文件都带 SPDX 与版权行。
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
用法: ci/gates/kernel-obs.sh [--src DIR]
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
FAILS=0

bad() { err "$*"; FAILS=$((FAILS + 1)); }

[ -d "${SRC}" ] || { err "内核源码目录不存在: ${SRC}"; exit "${EINVAL}"; }
[ -d "${OBS}" ] || { err "缺少观测面目录: ${OBS}"; exit "${EINVAL}"; }
[ -f "${OBS}/Kbuild" ] || { err "缺少 ${OBS}/Kbuild"; exit "${EINVAL}"; }

# 注册时序 -----------------------------------------------------------------
chk_place()
{
    local lsm="${SRC}/security/airy/airy_lsm.c"

    group "注册时序"
    grep -qE '^core_initcall\(airy_obs_start\);' "${OBS}/airy_obs_core.c" ||
        bad "顶层目录未在 core_initcall 发布"
    grep -q 'airy_probe_exit();' "${OBS}/airy_obs_core.c" ||
        bad "发布失败未撤回 agentrt 就绪标记"
    grep -qE 'kset_create_and_add\("airy"' "${OBS}/airy_obs_core.c" ||
        bad "未建立 /sys/airy 顶层 kset"
    if [ -f "${lsm}" ] &&
        grep -qE 'airy_obs_(init|start)|airy_obs\.h' "${lsm}"; then
        bad "LSM 初始化仍在注册观测面（kernfs 尚未就绪）"
    fi
    endgroup
}

# 属性契约 -----------------------------------------------------------------
chk_attrs()
{
    local f name

    group "属性契约"
    grep -q '__ATTR(_name, 0444, _name##_show, NULL)' "${OBS}/airy_obs.h" ||
        bad "AIRY_OBS_ATTR 未固定为 0444 只读"
    for f in "${OBS}"/*.c; do
        [ -f "${f}" ] || continue
        if grep -qE '(^|[^a-z_])__ATTR\(' "${f}"; then
            bad "$(basename "${f}"): 绕过 AIRY_OBS_ATTR 直接构造属性"
        fi
        if grep -qE 'static[[:space:]]+ssize_t[[:space:]]+[a-z0-9_]+_store' \
            "${f}"; then
            bad "$(basename "${f}"): 观测面出现写回调"
        fi
        if grep -qE '(^|[^a-z_])(scnprintf|snprintf|sprintf)\(' "${f}"; then
            bad "$(basename "${f}"): 输出未走 sysfs_emit"
        fi
        while IFS= read -r name; do
            grep -qE "${name}_show\\(" "${f}" ||
                bad "$(basename "${f}"): 属性 ${name} 缺少同名 show 回调"
        done < <(sed -n 's/.*AIRY_OBS_ATTR(\([a-z0-9_]*\)).*/\1/p' "${f}" |
            sort -u)
    done
    grep -q 'single_open(' "${OBS}/airy_obs_proc.c" ||
        bad "/proc/airy 未走 single_open"
    endgroup
}

# 源文件清单 ---------------------------------------------------------------
chk_kbuild()
{
    local listed actual

    group "源文件清单"
    listed=$(grep -oE '[a-z0-9_]+\.o' "${OBS}/Kbuild" | sed 's/\.o$//' |
        grep -vx airy_obs | sort -u)
    actual=$(cd "${OBS}" && for f in ./*.c; do printf '%s\n' "${f#./}"; done |
        sed 's/\.c$//' | sort -u)
    if [ "${listed}" != "${actual}" ]; then
        bad "Kbuild 清单与目录内容不一致"
        diff <(printf '%s\n' "${listed}") <(printf '%s\n' "${actual}") >&2 || true
    fi
    grep -qE '^obj-\$\(CONFIG_AIRY_OBS\)[[:space:]]*\+=[[:space:]]*obs/' \
        "${SRC}/kernel/Makefile" ||
        bad "kernel/Makefile 未引入 obs/ 子目录"
    endgroup
}

# 配置开关 -----------------------------------------------------------------
chk_config()
{
    local cfg

    group "配置开关"
    for cfg in configs/defconfig configs/defconfig-agent \
        configs/defconfig-embedded security/airy/.kunitconfig; do
        grep -qx 'CONFIG_AIRY_OBS=y' "${SRC}/${cfg}" ||
            bad "${cfg} 未开启 CONFIG_AIRY_OBS"
    done
    endgroup
}

# 文件头 -------------------------------------------------------------------
chk_headers()
{
    local f

    group "文件头"
    for f in "${OBS}"/*.c "${OBS}"/*.h; do
        [ -f "${f}" ] || continue
        sed -n '1p' "${f}" | grep -q 'SPDX-License-Identifier: GPL-2.0-only' ||
            bad "$(basename "${f}"): 首行缺少 SPDX"
        sed -n '1,5p' "${f}" | grep -q 'Copyright' ||
            bad "$(basename "${f}"): 缺少版权行"
    done
    sed -n '1p' "${OBS}/Kbuild" | grep -q 'SPDX-License-Identifier: GPL-2.0-only' ||
        bad "Kbuild: 首行缺少 SPDX"
    endgroup
}

chk_place
chk_attrs
chk_kbuild
chk_config
chk_headers

if [ "${FAILS}" -ne 0 ]; then
    err "观测面契约门禁失败 ${FAILS} 项"
    exit 1
fi

say "观测面契约门禁通过"
exit 0
