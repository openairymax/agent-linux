#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/tri-state.sh — 审计门禁自身的退出码协议
#
# 用法: ci/gates/tri-state.sh [--root DIR]
#
# 三态协议（0 通过／1 判负／3 不适用）要起作用，前提是每个门禁只说这三
# 种结论。四个判据对应四种漂移，任何一种都会让编排层把「跳过」读成「通
# 过」：头部没有退出码段（读者无从知道这个门禁说几种结论）、字面退出码
# 越界（有人新造了第四种结论）、EINVAL 取值分叉（同一类参数错误在不同
# 门禁退不同的码，统计与重试策略都失效）、语法错误（门禁根本没跑起来）。
#
# 只扫命令位的字面 `exit N`：夹具字符串里的 "exit 10" 是被测数据，不是
# 退出语句；`exit "${EINVAL}"` 由取值判据单独管，不在这里数。
#
# 退出码: 0 全部合规；1 有门禁不合规；5 参数或环境不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=lib/three-state.sh
. "${ROOT}/ci/gates/lib/three-state.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/tri-state.sh [--root DIR]

  --root DIR   被审计的仓库根，缺省为本脚本上两级
  -h           显示本帮助
EOF
}

# 头部必须有一段退出码说明，否则读者只能逐个分支反推结论种类。
hdr_bad()
{
    grep -q '^# 退出码:' "$1" || printf '%s: 缺少「退出码」段\n' "$1"
}

# 命令位的字面退出码只允许 0/1/3/5；引号内或变量形态不在此列。
code_bad()
{
    awk '/^[[:space:]]*exit[[:space:]]+[0-9]+[[:space:]]*(;|$)/ {
        line = $0
        sub(/^[[:space:]]*exit[[:space:]]+/, "", line)
        sub(/[^0-9].*$/, "", line)
        if (line != 0 && line != 1 && line != 3 && line != 5)
            printf "%s:%d: 退出码 %s 不在协议内\n", FILENAME, FNR, line
    }' "$1"
}

# EINVAL 是参数错误的唯一取值；各门禁可以自带声明，但值必须同为 5。
einv_bad()
{
    grep -nE '^[[:space:]]*readonly EINVAL=[0-9]+' "$1" |
        grep -vE '=5[[:space:]]*$' |
        sed "s|^|$1:|"
}

# 语法错误的门禁不会产出判定，只会产出一行 shell 报错。
syn_bad()
{
    local msg
    if ! msg="$(bash -n "$1" 2>&1)"; then
        printf '%s: 语法错误: %s\n' "$1" "${msg//$'\n'/ }"
    fi
}

# 逐个门禁跑四项判据，全绿才算这条门禁守协议。
scan()
{
    local dir="$1" f total=0 bad=0 out

    for f in "${dir}"/*.sh; do
        [ -f "${f}" ] || continue
        total=$((total + 1))
        out="$(hdr_bad "${f}"; code_bad "${f}"; einv_bad "${f}"; syn_bad "${f}")"
        if [ -n "${out}" ]; then
            printf '%s\n' "${out}"
            bad=$((bad + 1))
        fi
    done
    if [ "${total}" -eq 0 ]; then
        err "未找到门禁: ${dir}"
        exit "${CI_EINVAL}"
    fi
    say "门禁 ${total} 条，不合规 ${bad} 条"
    [ "${bad}" -eq 0 ]
}

root="${ROOT}"
while [ "$#" -gt 0 ]; do
    case "$1" in
    --root)
        [ "$#" -ge 2 ] || { err "--root 需要目录"; exit "${CI_EINVAL}"; }
        root="$2"
        shift
        ;;
    -h|--help) usage; exit "${TS_PASS}" ;;
    *) err "未知参数: $1"; exit "${CI_EINVAL}" ;;
    esac
    shift
done

[ -d "${root}/ci/gates" ] || {
    err "门禁目录不存在: ${root}/ci/gates"
    exit "${CI_EINVAL}"
}

scan "${root}/ci/gates"
