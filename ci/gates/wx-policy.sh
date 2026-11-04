#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/wx-policy.sh — W^X 例外清单与绕过禁令门禁（I-27 / D-31）
#
# 用法:
#   ci/gates/wx-policy.sh [--tree DIR] [--root DIR] [--live]
#
# 背景：ALK 强制 W^X 之后，声明 PT_GNU_STACK=RWE 的共享库在 dlopen 阶段被拒
# ——glibc 要把栈改成可执行，撞的是同一条 W→X 路径。0.0.3 在验证机上清掉库
# 的那一位取证，那是取证手段而非修法：写进发行版等于要求用户改厂商库，且
# 厂商一升级就失效。真正的修法是内核受理一份白名单并据此放行，这需要
# file_mprotect 钩子与可写策略通道；0.0.4 内核侧净新增 0 行（方案 §0 铁律
# ①与 ⑤），故白名单登记顺延（§2.3），本门禁改为钉住顺延期的两件事：不许
# 用绕过手段，以及有多少库会在装机后撞墙。
#
# 声明面（任何机器可跑）：
#
#   * 回归器存在、可被 Python 编译、无桩标记、带四个结论标记与 PT_GNU_STACK
#     解析——少了任何一条，实物面的结论都不可信。
#   * 交付链里不出现 execstack -c / paxctl / -z execstack / noexecstack：清位
#     就是要求用户改厂商库，重编成可执行栈就是整体关 W^X，两者都属判据③
#     明令禁止的「整体关闭 W^X」。
#   * 跑一遍纯扫描面，确认 ELF 解析在本树可用（不碰内核）。
#
# 实物面：
#
#   * --root DIR：列出镜像内 PT_GNU_STACK 可执行的 ELF。这是顺延期的例外
#     清单，不是白名单——清单决定顺延期间哪些软件会在加载阶段失败。
#   * --live：现场合成一对共享库（同源，只差 -z execstack）并真实 dlopen。
#     先立阴性对照（普通库必须能加载，否则拒绝来自环境而非 W^X），再判
#     可执行栈库被拒且语义是权限拒绝、W→X mprotect 仍被拒、mmap(W|X) 仍
#     放行。本机不是 AirymaxOS 内核时判「不适用」，打印所需内核能力并以 5
#     退出——不适用不等于通过。
#
# 退出码: 0 通过；1 检查失败；5 参数或环境不满足（含不适用）。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
SYSROOT=""
LIVE=0
FAILS=0

usage()
{
    cat <<'EOF'
用法: ci/gates/wx-policy.sh [--tree DIR] [--root DIR] [--live]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tree)
            [ -n "${2:-}" ] || { err "--tree 需要目录路径"; exit "${EINVAL}"; }
            [ -d "$2" ] || { err "--tree 目录不存在: $2"; exit "${EINVAL}"; }
            TREE="$2"
            shift 2
            ;;
        --root)
            [ -n "${2:-}" ] || { err "--root 需要目录路径"; exit "${EINVAL}"; }
            [ -d "$2" ] || { err "--root 目录不存在: $2"; exit "${EINVAL}"; }
            SYSROOT="$2"
            shift 2
            ;;
        --live) LIVE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TREE}" ] || TREE="${ROOT}"
TOOL="${AIRY_WX_TOOL:-${TREE}/ci/tools/execstack_regress.py}"

bad() { err "$*"; FAILS=$((FAILS + 1)); }

# 声明面 -------------------------------------------------------------------

chk_tool()
{
    [ -f "${TOOL}" ] || { bad "缺少 ${TOOL#"${TREE}"/}"; return 0; }
    [ -x "${TOOL}" ] || bad "回归器不可执行"
    [ "$(sed -n '1p' "${TOOL}")" = '#!/usr/bin/env python3' ] ||
        bad "首行不是 python3 shebang"
    python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' \
        "${TOOL}" 2>/dev/null || bad "回归器语法不通过"
    if grep -nE 'TODO|FIXME|XXX' "${TOOL}"; then
        bad "回归器存在桩标记"
    fi
    for tag in AIRY-WX-PASS AIRY-WX-FAIL AIRY-WX-WAIVED; do
        grep -q "${tag}" "${TOOL}" || bad "回归器缺少 ${tag}"
    done
    grep -q 'p_type == PT_GNU_STACK' "${TOOL}" || bad "回归器缺少 PT_GNU_STACK 解析"
}

# 绕过禁令：交付链里只允许存在「撞墙清单」，不允许清位或重编来绕过。
chk_bypass()
{
    local hits

    hits=$(grep -rnE 'execstack[[:space:]]+-c|paxctl|-z[[:space:]]*execstack|noexecstack' \
        "${TREE}/distro" "${TREE}/system" 2>/dev/null | grep -vE ':[[:space:]]*#')
    if [ -n "${hits}" ]; then
        printf '%s\n' "${hits}" >&2
        bad "交付链出现 W^X 绕过手段（清位或重编可执行栈）"
    fi
}

chk_scan()
{
    local out

    out=$(python3 "${TOOL}" --scan "${TREE}/distro" --no-probe --quiet 2>&1) ||
        { bad "扫描面不可用"; return 0; }
    case "${out}" in
        *"SCAN "*" rwe="*) say "扫描面可用" ;;
        *) bad "扫描面未给出汇总行" ;;
    esac
}

chk_decl()
{
    say "声明面 ${TREE}"
    chk_tool
    chk_bypass
    [ -f "${TOOL}" ] && chk_scan
}

# 实物面 -------------------------------------------------------------------

scan_root()
{
    local out hits rc

    out=$(python3 "${TOOL}" --scan "${SYSROOT}" --no-probe --quiet 2>&1)
    rc=$?
    [ "${rc}" -eq 0 ] || { bad "镜像扫描失败"; printf '%s\n' "${out}" >&2; return 0; }
    printf '%s\n' "${out}" | grep '^RWE  ' || note "镜像内没有可执行栈的 ELF"
    hits=$(printf '%s\n' "${out}" | sed -n 's/^SCAN .* rwe=//p')
    say "例外清单 ${SYSROOT} rwe=${hits:-?}"
}

live_probe()
{
    local out rc

    group "实机判定"
    for c in python3 gcc; do
        command -v "${c}" >/dev/null 2>&1 || {
            err "缺少命令: ${c}"
            exit "${EINVAL}"
        }
    done
    if [ -n "${SYSROOT}" ]; then
        out=$(python3 "${TOOL}" --scan "${SYSROOT}" 2>&1)
    else
        out=$(python3 "${TOOL}" 2>&1)
    fi
    rc=$?
    printf '%s\n' "${out}"
    endgroup
    case "${rc}" in
        0) say "W^X 判据成立" ;;
        1) err "W^X 判据不成立"; exit 1 ;;
        4)
            note "不适用：本机不是 AirymaxOS 内核，W^X 未由 airy 强制"
            note "受理白名单所需内核能力：file_mprotect 钩子 + 可执行栈例外字段"
            note "                          + 可写策略通道（0.0.4 顺延，见方案 §2.3）"
            exit "${EINVAL}"
            ;;
        *) err "回归器退出码 ${rc}"; exit "${EINVAL}" ;;
    esac
}

chk_decl
[ -z "${SYSROOT}" ] || scan_root
[ "${LIVE}" -eq 0 ] || live_probe

if [ "${FAILS}" -ne 0 ]; then
    err "W^X 门禁失败 ${FAILS} 项"
    exit 1
fi

say "W^X 门禁通过"
exit 0
