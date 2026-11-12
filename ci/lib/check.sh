#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/check.sh — 「造实物 + 正负例」门禁的通用骨架
#
# 目的：骨架里有四段此前逐字抄了多份——EXIT 时清临时目录、按项累计失败、
# 入口可执行与 python3 shebang 自检、单目录选项 --tree 的解析与兜底。
# llm-offline 与 model-manifest 的 check_entry 前两行、backlight 与
# desktop-capability 的 bad() / cleanup() / while-case 各有一份；一份改了
# 另一份不会知道，同一条判据于是分叉。
#
# 调用方：ci/gates/{llm-offline,model-manifest,backlight,desktop-capability}.sh。
# 约定：先 source ci/lib/log.sh（依赖 err / die / need_dir / CI_EINVAL），并
# 在 source 本文件之前声明 ROOT 与要用的状态变量（TMP / FAILS / TREE）；
# 本文件只提供函数，不建临时目录、不清理自己、不打印用法。
# ===========================================================================

# cleanup —— trap EXIT 用；临时目录没建或已清时静默返回。
cleanup()
{
    [ -n "${TMP}" ] && rm -rf "${TMP}"
    return 0
}

# bad MSG... —— 记一条失败并累计，不中断判定，便于一次报全。
bad()
{
    err "$*"
    FAILS=$((FAILS + 1))
}

# chk_entry BIN —— 门禁入口须可执行，且首行为 python3 shebang。
chk_entry()
{
    local bin="$1"
    [ -x "${bin}" ] || die "入口不可执行: ${bin#"${ROOT}/"}"
    [ "$(sed -n '1p' "${bin}")" = '#!/usr/bin/env python3' ] ||
        die "入口首行不是 python3 shebang"
}

# opts_dir ARGS... —— 单目录选项门禁的解析：只认 --tree DIR，未给取 ROOT。
#
# 缺参、目录不存在、未知选项一律退 CI_EINVAL，与「判定失败」（退 1）区分；
# 未知选项不打印用法，与原手写的 while/case 逐字一致。TREE 与 ROOT 由调用
# 方声明，本函数只赋值并做兜底。
opts_dir()
{
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --tree) need_dir "$1" "${2:-}"; TREE="$2"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) err "未知参数: $1"; exit "${CI_EINVAL}" ;;
        esac
    done
    [ -n "${TREE}" ] || TREE="${ROOT}"
}
