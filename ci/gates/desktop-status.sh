#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/desktop-status.sh — 桌面状态面板门禁（R1-14 / 判据⑨①）
#
# 用法:
#   ci/gates/desktop-status.sh [--tree DIR]
#
# 背景：磁盘、网络、模型三项判定此前只在三个命令的退出码里，桌面用户既
# 不知道该跑哪条命令，也不知道该看哪一行；现场「状态看不见」于是退化成
# 「大概正常」。本版把三项摊进应用菜单里的一个入口（airy-status），判定
# 仍由三个既有生产者给出，面板只做汇总与缺项显式化。
#
# 声明面：
#
#   * 面板可执行、python3 首行、语法通过，且确实调用三个生产者——
#     少一个就说明有一项被悄悄漏出面板。
#   * 菜单项 Exec 指向 /usr/bin/airy-status、Terminal=true、中文名存在、
#     没有 NoDisplay——菜单里看不见的面板等于没做。
#   * 打包三处同源：spec 声明 Source、安装实现与符号链接、%files 收全。
#
# 实物面（桩生产者，全部判定与真机无关）：
#
#   * 三项齐备：三行汇总都出、末行 status=ok、退 0。
#   * 模型缺件：model state=absent、status=degraded、退 1，磁盘与网络照旧。
#   * 磁盘输出非 JSON：disk=absent、status=degraded、退 1。
#   * 模型降级（退 10）：status=degraded、退 1，state 行仍可读。
#   * --check：生产者齐备判 ok；空 PATH 判 degraded。
#
# 退出码: 0 通过；1 检查失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
TMP=""
FAILS=0

PY="$(command -v python3 || true)"

usage()
{
    cat <<'EOF'
用法: ci/gates/desktop-status.sh [--tree DIR]
EOF
}

# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

trap cleanup EXIT

opts_dir "$@"

TMP="$(mktemp -d)"

APP="${TREE}/distro/desktop/airy_status.py"
ENTRY="${TREE}/distro/desktop/airymax-status.desktop"
SPEC="${TREE}/distro/specs/airymaxos-release.spec"

# chk_app —— 实现面：面板可执行、语法通过、确实调用三个生产者。
chk_app()
{
    [ -n "${PY}" ] || die "缺少命令: python3"
    [ -f "${APP}" ] || { bad "缺少 distro/desktop/airy_status.py"; return 0; }
    chk_entry "${APP}"
    "${PY}" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "${APP}" ||
        bad "airy_status.py 语法不通过"
    for peer in airy-disk airy-observe airy-model; do
        grep -qF "${peer}" "${APP}" || bad "面板未调用生产者 ${peer}"
    done
}

# chk_menu —— 菜单项：可点、可见、中文名齐备。
chk_menu()
{
    [ -f "${ENTRY}" ] || { bad "缺少 airymax-status.desktop"; return 0; }
    grep -qxF 'Exec=/usr/bin/airy-status' "${ENTRY}" ||
        bad "菜单项 Exec 不是 /usr/bin/airy-status"
    grep -qxF 'Terminal=true' "${ENTRY}" || bad "菜单项不是终端型"
    grep -qF 'Name[zh_CN]=' "${ENTRY}" || bad "菜单项缺中文名"
    if grep -qxF 'NoDisplay=true' "${ENTRY}"; then
        bad "菜单项带 NoDisplay=true，用户找不到"
    fi
}

# chk_spec —— 打包三处同源：声明、安装、收件。
chk_spec()
{
    [ -f "${SPEC}" ] || { bad "缺少 airymaxos-release.spec"; return 0; }
    grep -qF 'Source51:       airy_status.py' "${SPEC}" ||
        bad "spec 未声明 airy_status.py"
    grep -qF 'Source52:       airymax-status.desktop' "${SPEC}" ||
        bad "spec 未声明 airymax-status.desktop"
    grep -qF 'desktop/airy_status.py' "${SPEC}" ||
        bad "spec 未安装面板实现"
    grep -qF '%{_bindir}/airy-status' "${SPEC}" ||
        bad "spec 未建 /usr/bin/airy-status"
    grep -qF 'applications/airymax-status.desktop' "${SPEC}" ||
        bad "spec 未安装菜单项"
}

# chk_decl —— 声明面：实现、菜单项与打包三处必须同源。
chk_decl()
{
    say "声明面 ${TREE}"
    chk_app
    chk_menu
    chk_spec
}

# stub DIR NAME BODY —— 造一个生产者桩；只依赖 shell 内建，PATH 受控也能跑。
stub()
{
    printf '#!/bin/sh\n%s\n' "$3" >"$1/$2"
    chmod +x "$1/$2"
}

# good DIR —— 三项齐备的桩集合。
good()
{
    local d="$1"

    mkdir -p "${d}"
    stub "${d}" airy-disk \
        "printf '%s\n' '{\"schema\":1,\"kind\":\"single\",\"install\":{\"verdict\":\"match\",\"layout\":\"single\",\"disk\":\"nvme0n1\"},\"disks\":[{\"name\":\"nvme0n1\",\"parts\":[{\"name\":\"nvme0n1p3\",\"role\":\"system\",\"mount\":\"/\"},{\"name\":\"nvme0n1p5\",\"role\":\"data\",\"mount\":\"/data\"}]}]}'"
    stub "${d}" airy-observe \
        "printf 'AIRYMAX-OBSERVE net.ipv4=eth0=192.168.3.57\n'
printf 'AIRYMAX-OBSERVE net.src=dhcp\n'
printf 'AIRYMAX-OBSERVE net.gw=192.168.3.1\n'
printf 'AIRYMAX-OBSERVE net.dns=192.168.3.1\n'"
    stub "${d}" airy-model \
        "printf '{\"state\":\"ready\",\"backend\":\"cpu\",\"reason\":null,\"digest\":\"0123456789abcdef\"}\n'"
}

# panel DIR —— 以 DIR 为唯一 PATH 跑面板；python3 用绝对路径，否则解释器
# 自己也在 PATH 里找不到。
panel()
{
    PATH="$1" "${PY}" "${APP}" 2>&1
}

# want NAME RC OUT NEEDLE WANT_RC —— 一例一断言，失败即打印全量输出。
want()
{
    local name="$1" rc="$2" out="$3" needle="$4" wrc="$5"

    if [ "${rc}" != "${wrc}" ]; then
        bad "${name}（rc=${rc}，期望 ${wrc}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) say "ok ${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

chk_obj()
{
    local d="${TMP}/run" out rc

    say "实物面"
    good "${d}"
    out="$(panel "${d}")"
    rc=$?
    want "三项齐备判 ok" "${rc}" "${out}" 'status=ok' 0
    want "磁盘行含安装期结论" "${rc}" "${out}" \
        'disk install=match layout=single disk=nvme0n1' 0
    want "分区角色可读" "${rc}" "${out}" 'disk part nvme0n1p3 role=system mount=/' 0
    want "网络四元组可读" "${rc}" "${out}" \
        'net ipv4=eth0=192.168.3.57 src=dhcp gw=192.168.3.1 dns=192.168.3.1' 0
    want "模型摘要可读" "${rc}" "${out}" 'model state=ready backend=cpu' 0

    good "${TMP}/nomodel"
    stub "${TMP}/nomodel" airy-model 'exit 1'
    out="$(panel "${TMP}/nomodel")"
    rc=$?
    want "模型缺件退 1" "${rc}" "${out}" 'model state=absent' 1
    want "模型缺件末行降级" "${rc}" "${out}" 'status=degraded' 1

    good "${TMP}/nodisk"
    stub "${TMP}/nodisk" airy-disk 'printf "not-json\n"'
    out="$(panel "${TMP}/nodisk")"
    rc=$?
    want "磁盘输出不可解析判缺件" "${rc}" "${out}" 'disk=absent' 1

    good "${TMP}/degraded"
    stub "${TMP}/degraded" airy-model \
        "printf '{\"state\":\"degraded\",\"backend\":\"cpu\",\"reason\":\"no-accel\",\"digest\":\"\"}\n'
exit 10"
    out="$(panel "${TMP}/degraded")"
    rc=$?
    want "模型降级仍可读" "${rc}" "${out}" \
        'model state=degraded backend=cpu reason=no-accel' 1
    want "模型降级末行降级" "${rc}" "${out}" 'status=degraded' 1

    out="$(PATH="${d}" "${PY}" "${APP}" --check 2>&1)"
    rc=$?
    want "自检生产者齐备判 ok" "${rc}" "${out}" 'status=ok' 0
    out="$(PATH="/nonexistent" "${PY}" "${APP}" --check 2>&1)"
    rc=$?
    want "自检空 PATH 判降级" "${rc}" "${out}" 'status=degraded' 1
}

chk_decl
chk_obj

if [ "${FAILS}" -gt 0 ]; then
    err "桌面状态判定失败：${FAILS} 项"
    exit 1
fi
say "桌面状态判定通过"
exit 0
