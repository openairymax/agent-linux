#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/desktop-capability.sh — 显示能力判定与不适用策略门禁（I-26 / HW-06）
#
# 用法:
#   ci/gates/desktop-capability.sh [--tree DIR]
#
# 背景：0.0.3 的座位封装只看 sysfs 驱动名，simpledrm 之外一律交给 X 自动
# 选择。驱动名只能说明内核绑上了显卡，说明不了这块屏能用：面板未点亮、
# EDID 没读到、只剩未连接端口时，X 会在模式求交上失败，表现为「进了桌面
# 但黑屏」，而日志里看不出是哪一条不成立。0.0.4 的判据改为三条同时成立：
# 真实 KMS 驱动、连接器已连接、该连接器同时有可枚举模式与非空 EDID。缺任
# 一条落 fbdev 并把缺口写进标记；两样都没有（无卡也无 framebuffer）时判
# 「不适用」，不静默跳过。
#
# 声明面：
#
#   * 封装存在、POSIX 语法通过、首行为 /bin/sh、带 AIRYMAX-XSERVER 标记。
#   * 没有桩标记；代码里不出现具体显卡型号名——判定只认驱动类型与连接器
#     状态，换机器不改代码。
#   * smoke T1 必须核对驱动标记，否则状态文件只是没人读的日志。
#
# 实物面（临时 DRM 树 + 伪 Xorg，全部判定与真机内核无关）：
#
#   * 三条全成立：不传 -config、不生成配置，标记 kms=1。
#   * 缺 EDID、缺模式、只有未连接输出、只有 simpledrm：四种缺口各落一次
#     fbdev，reason 与告警里的缺口一致。
#   * 无卡且无 framebuffer：不适用（note=no-display），既不传配置也不报错。
#   * 兜底配置写不进去：退回自动选择，标记如实写 conf-unwritable。
#   * 每个用例的标记都落盘，且与 stdout 同一行——现场只认这一行。
#
# 退出码: 0 通过；1 检查失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly TAG="AIRYMAX-XSERVER"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
TMP=""
FAILS=0
CODE=0
OUT=""
ERR=""

usage()
{
    cat <<'EOF'
用法: ci/gates/desktop-capability.sh [--tree DIR]
EOF
}

cleanup()
{
    [ -n "${TMP}" ] && rm -rf "${TMP}"
    return 0
}

trap cleanup EXIT

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tree)
            [ -n "${2:-}" ] || { err "--tree 需要目录路径"; exit "${EINVAL}"; }
            [ -d "$2" ] || { err "--tree 目录不存在: $2"; exit "${EINVAL}"; }
            TREE="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TREE}" ] || TREE="${ROOT}"

bad() { err "$*"; FAILS=$((FAILS + 1)); }

# 声明面 -------------------------------------------------------------------

chk_pkg()
{
    local script="$1/distro/desktop/xserver.sh"

    say "声明面 ${1}"
    [ -f "${script}" ] || { bad "缺少 distro/desktop/xserver.sh"; return 0; }
    sh -n "${script}" || bad "xserver.sh 语法不通过"
    [ "$(sed -n '1p' "${script}")" = '#!/bin/sh' ] ||
        bad "首行不是 /bin/sh shebang"
    [ -x "${script}" ] || bad "xserver.sh 不可执行"
    grep -q "${TAG}" "${script}" || bad "缺少 ${TAG} 标记"
    if grep -nE 'TODO|FIXME' "${script}"; then
        bad "存在桩标记"
    fi
    # 机型名只允许出现在注释里；代码行按驱动类型与连接器状态判定。
    if grep -nE 'intel|nvidia|amdgpu|radeon|i915|nouveau' "${script}" |
        grep -vE '^[0-9]+:[[:space:]]*#'; then
        bad "代码里硬编码了显卡型号"
    fi
    grep -q 'seat_marker' "$1/distro/ci/run-smoke.sh" ||
        bad "smoke T1 未核对驱动标记"
}

# 实物面 -------------------------------------------------------------------

mkcard()
{
    local target="${TMP}/drv/$1"

    mkdir -p "${target}" "${DRM}/card0/device"
    ln -s "${target}" "${DRM}/card0/device/driver"
}

# mkout NAME STATUS MODES EDID —— 铺一个连接器；空参数即该节点缺失内容。
mkout()
{
    local out="${DRM}/card0-$1"

    mkdir -p "${out}"
    printf '%s\n' "$2" >"${out}/status"
    [ -z "$3" ] || printf '%s\n' "$3" >"${out}/modes"
    [ -z "$4" ] || printf 'edid' >"${out}/edid"
}

start()
{
    rm -rf "${DRM}" "${RUN}" "${FB}"
    mkdir -p "${DRM}" "${RUN}"
    : >"${FB}"
}

run()
{
    OUT=$(AIRYMAX_XSERVER_CONF="${1:-${CONF}}" "${SEAT}" :0 2>"${TMP}/err")
    CODE=$?
    ERR=$(cat "${TMP}/err")
}

mark_is()
{
    local want="${TAG} $1" what="$2" line

    line=$(sed -n "s/^${TAG} //p" "${RUN}/xserver.log" 2>/dev/null)
    printf '%s\n' "${OUT}" | grep -qxF "${want}" ||
        bad "${what}: stdout 缺标记 \"${want}\""
    [ "${line}" = "${want#* }" ] ||
        bad "${what}: 状态文件为 \"${line}\"，与标记不一致"
}

has_arg()
{
    printf '%s\n' "${OUT}" | grep -qxF "ARG $1"
}

fbdev_is()
{
    local gap="$1" what="$2"

    [ "${CODE}" -eq 0 ] || bad "${what}: 退出码 ${CODE}"
    mark_is "driver=fbdev reason=${gap} fb=${FB} conf=${CONF}" "${what}"
    has_arg -config && has_arg "${CONF}" ||
        bad "${what}: X 未拿到 -config ${CONF}"
    printf '%s\n' "${ERR}" | grep -qF "display gap ${gap}" ||
        bad "${what}: 告警里没有缺口 ${gap}"
    grep -qF 'Driver "fbdev"' "${CONF}" || bad "${what}: 配置不是 fbdev"
}

chk_tree()
{
    say "实物面（临时 DRM 树 + 伪 Xorg）"

    start
    mkcard i915
    mkout DP-1 connected 1920x1080 edid
    run
    [ "${CODE}" -eq 0 ] || bad "kms: 退出码 ${CODE}"
    mark_is "driver=i915 kms=1" "kms"
    has_arg -config && bad "kms: 不应传 -config"
    [ -e "${CONF}" ] && bad "kms: 不应生成 fbdev 配置"

    start
    mkcard i915
    mkout DP-1 connected 1920x1080 ""
    run
    fbdev_is no-edid "no-edid"

    start
    mkcard i915
    mkout DP-1 connected "" edid
    run
    fbdev_is no-mode "no-mode"

    start
    mkcard i915
    mkout DP-1 disconnected 1920x1080 edid
    run
    fbdev_is no-output "no-output"

    # simpledrm 也报模式与 EDID，但 X 1.20 的 modesetting 不接管它。
    start
    mkcard simple-framebuffer
    mkout Virtual-1 connected 1024x768 edid
    run
    fbdev_is no-kms "simpledrm"

    start
    rm -f "${FB}"
    run
    [ "${CODE}" -eq 0 ] || bad "absent: 退出码 ${CODE}"
    mark_is "driver=none note=no-display" "absent"
    has_arg -config && bad "absent: 不应传 -config"

    start
    mkcard simple-framebuffer
    : >"${TMP}/block"
    run "${TMP}/block/x.conf"
    [ "${CODE}" -eq 0 ] || bad "unwritable: 退出码 ${CODE}"
    mark_is "driver=fbdev reason=no-kms note=conf-unwritable" "unwritable"
    has_arg -config && bad "unwritable: 不应传 -config"
}

TMP="$(mktemp -d)"
SEAT="${TREE}/distro/desktop/xserver.sh"
DRM="${TMP}/drm"
FB="${TMP}/fb0"
RUN="${TMP}/run"
CONF="${RUN}/xserver-fbdev.conf"
export AIRYMAX_DRM_DIR="${DRM}"
export AIRYMAX_FB_DEV="${FB}"
export AIRYMAX_XSERVER_CONF="${CONF}"
export AIRYMAX_XSERVER_STATE="${RUN}/xserver.log"
export AIRYMAX_XORG="${TMP}/Xorg"
printf '#!/bin/sh\nprintf "ARG %%s\\n" "$@"\n' >"${AIRYMAX_XORG}"
chmod +x "${AIRYMAX_XORG}"

chk_pkg "${TREE}"
[ -f "${SEAT}" ] && chk_tree

if [ "${FAILS}" -ne 0 ]; then
    err "显示能力判定失败 ${FAILS} 项"
    exit 1
fi

say "显示能力判定通过"
exit 0
