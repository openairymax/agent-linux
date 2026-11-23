#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/check.sh — 「造实物 + 正负例」门禁的通用骨架
#
# 目的：骨架里有四段此前逐字抄了多份——EXIT 时清临时目录、按项累计失败、
# 入口可执行与 python3 shebang 自检、单目录选项 --tree 的解析与兜底。
# llm-offline 与 model-manifest 的 check_entry 前两行、backlight 与
# desktop-capability 的 bad() / cleanup() / while-case 各有一份；一份改了
# 另一份不会知道，同一条判据于是分叉。
#
# 调用方：ci/gates/{llm-offline,model-manifest,backlight,desktop-capability,
# charset,locale-matrix}.sh。
# 约定：先 source ci/lib/log.sh（依赖 err / die / need_dir / CI_EINVAL），并
# 在 source 本文件之前声明 ROOT 与要用的状态变量（TMP / FAILS / TREE）；
# 本文件只提供函数，不建临时目录、不清理自己、不打印用法。
#
# 断言原语（need_file / need_exe / grep_in / grep_re / code_is / out_has）是
# 「一个判据一行」的写法：`[ -f x ] || bad y` 与 `grep -q p f || bad y` 在
# 每个门禁里重复几十次，判定点也就跟着重复累计；收在这里后，新增判据只加
# 数据不加分支，圈复杂度不再随检查项线性上涨。
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

# gate_boot —— 门禁统一起步：状态清零并挂退出清理。
#
# TREE/TMP/FAILS/CODE/OUT 的清零与 trap 此前在每个门禁里逐字重复；漏一行
# 就会让失败计数或临时目录跨门禁残留。source 本文件后调用一次，再按需用
# opts_dir 解析 --tree。
gate_boot()
{
    TREE=""
    TMP=""
    FAILS=0
    CODE=0
    OUT=""
    trap cleanup EXIT
}

# run_bin CMD... —— 跑一次外部命令，收合并输出与真实退出码。
#
# 判定类门禁断言的是「退出码是几」，不是「命令成功」，所以非零不许中断；
# 返回值恒 0，让调用方接着用 code_is 报具体差值。
run_bin()
{
    OUT=$("$@" 2>&1)
    CODE=$?
    return 0
}

# need_file PATH 说明 —— 缺件记一次失败；失败返回 1 供需要提前收尾的调用方。
need_file()
{
    [ -f "$1" ] || { bad "缺少 $2"; return 1; }
}

# need_exe PATH 说明 —— 入口须在位且可执行，语义同上。
need_exe()
{
    [ -x "$1" ] || { bad "$2 不在位或不可执行"; return 1; }
}

# grep_in TEXT PATH 说明 —— 文件须含该定值文本（分隔符与元字符按字面比）。
grep_in()
{
    grep -qF -- "$1" "$2" || bad "$3"
}

# grep_re PATTERN PATH 说明 —— 文件须匹配该扩展正则。
grep_re()
{
    grep -qE -- "$1" "$2" || bad "$3"
}

# code_is WANT 说明 —— 断言上一条 run_bin 的退出码。
code_is()
{
    [ "${CODE}" -eq "$1" ] || bad "$2（rc=${CODE}）"
}

# out_has TEXT 说明 —— 断言上一条 run_bin 的输出含该段文本。
out_has()
{
    case "${OUT}" in
        *"$1"*) return 0 ;;
    esac
    bad "$2"
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
