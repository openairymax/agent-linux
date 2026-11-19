#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tools/tui-field-collect.sh — 目标机取证记录采集器
#
# 用法:
#   ci/tools/tui-field-collect.sh --out DIR --case NAME --observed TEXT
#       [--result pass|fail|unavailable|blocked] [--note TEXT]
#       [--screenshot FILE]
#   ci/tools/tui-field-collect.sh --fixture --out DIR --case NAME
#       --arch ARCH --cols N --rows N --observed TEXT [--result ...]
#
# 采集器只做一件事：把「操作者在目标机上看到的事实」落成一条记录。机器
# 身份、构建号、终端尺寸取自本机，不接受命令行直接声明——一旦允许声明，
# 记录就不再是读数。唯一例外是 --fixture：它写 evidence=fixture 并强制
# fixture- 前缀，永远不会被发版覆盖统计采信。
#
# 实机模式要求本机确实是 AirymaxOS：/etc/os-release 的 ID 必须是 airymaxos，
# /etc/airymaxos-build 必须给出 BUILD_ID。开发机跑不出实机记录不是缺陷，
# 是这层校验在起作用。
#
# 记录格式与判定见 10-references/tui-field-evidence.md 与
# crates/boot-tui/tests/field/main.rs。退出码: 0 写入；1 本机不满足；
# 5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

readonly TOOL_EINVAL=5
readonly CASES="ime-recover ime-no-dict asr-unavailable probe-stale render-slow inject-unknown tty-small display-zh"
readonly ARCHES="x86_64 aarch64 riscv64"
readonly RESULTS="pass fail unavailable blocked"
readonly RELEASE="/etc/os-release"
readonly BUILD="/etc/airymaxos-build"

OUT=""
CASE=""
OBS=""
RESULT="pass"
NOTE=""
SHOT=""
ARCH=""
COLS=""
ROWS=""
FIXTURE=0

usage()
{
    cat <<'EOF'
用法: ci/tools/tui-field-collect.sh --out DIR --case NAME --observed TEXT
                                  [--result R] [--note TEXT] [--screenshot FILE]
       ci/tools/tui-field-collect.sh --fixture --out DIR --case NAME
                                  --arch A --cols N --rows N --observed TEXT
EOF
}

# has_word WORD LIST —— 词表成员判定；LIST 以空格分隔。
has_word()
{
    case " $2 " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# word_of KEY FILE —— 取 KEY=VALUE 形式的第一个值。
word_of()
{
    sed -n "s/^$1=//p" "$2" 2>/dev/null | head -n 1 | tr -d '"'
}

# clean VALUE WHAT —— 值必须单行且非空，否则记录会被截断。
clean()
{
    [ -n "$1" ] || die "$2 不能为空"
    case "$1" in
        *$'\n'*|*$'\r'*) die "$2 不能包含换行" ;;
    esac
}

take()
{
    [ -n "$2" ] || { err "$1 需要取值"; exit "${TOOL_EINVAL}"; }
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out) take "$1" "${2:-}"; OUT="$2"; shift 2 ;;
        --case) take "$1" "${2:-}"; CASE="$2"; shift 2 ;;
        --observed) take "$1" "${2:-}"; OBS="$2"; shift 2 ;;
        --result) take "$1" "${2:-}"; RESULT="$2"; shift 2 ;;
        --note) take "$1" "${2:-}"; NOTE="$2"; shift 2 ;;
        --screenshot) take "$1" "${2:-}"; SHOT="$2"; shift 2 ;;
        --arch) take "$1" "${2:-}"; ARCH="$2"; shift 2 ;;
        --cols) take "$1" "${2:-}"; COLS="$2"; shift 2 ;;
        --rows) take "$1" "${2:-}"; ROWS="$2"; shift 2 ;;
        --fixture) FIXTURE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${TOOL_EINVAL}" ;;
    esac
done

[ -n "${OUT}" ] || { err "--out 必填"; exit "${TOOL_EINVAL}"; }
[ -n "${CASE}" ] || { err "--case 必填"; exit "${TOOL_EINVAL}"; }
has_word "${CASE}" "${CASES}" || { err "未知用例: ${CASE}"; exit "${TOOL_EINVAL}"; }
has_word "${RESULT}" "${RESULTS}" || { err "未知判定: ${RESULT}"; exit "${TOOL_EINVAL}"; }
clean "${OBS}" "--observed"
[ -z "${NOTE}" ] || clean "${NOTE}" "--note"

if [ "${FIXTURE}" -eq 1 ]; then
    [ -n "${ARCH}" ] && [ -n "${COLS}" ] && [ -n "${ROWS}" ] ||
        { err "夹具模式必须显式给出 --arch/--cols/--rows"; exit "${TOOL_EINVAL}"; }
    EVIDENCE="fixture"
    LABEL="fixture-${CASE}"
    OS="AirymaxOS 0.0.5"
    BUILD_ID="fixture-format"
    TERM_ID="${TERM:-linux}"
    WHEN=""
    HOST=""
else
    need_cmd sed head tr cut uname stty date hostname sha256sum mkdir mv
    [ -r "${RELEASE}" ] || die "缺少 ${RELEASE}：不是 AirymaxOS 目标机"
    [ "$(word_of ID "${RELEASE}")" = "airymaxos" ] ||
        die "${RELEASE} 的 ID 不是 airymaxos：不是 AirymaxOS 目标机"
    OS="$(word_of PRETTY_NAME "${RELEASE}")"
    clean "${OS}" "os-release PRETTY_NAME"
    [ -r "${BUILD}" ] || die "缺少 ${BUILD}：无法追溯构建号"
    BUILD_ID="$(word_of BUILD_ID "${BUILD}")"
    clean "${BUILD_ID}" "BUILD_ID"
    ARCH="$(uname -m)"
    case "${ARCH}" in
        arm64) ARCH="aarch64" ;;
    esac
    [ -t 0 ] || die "实机取证必须在终端里采集（需要读取终端尺寸）"
    read -r ROWS COLS < <(stty size)
    TERM_ID="${TERM:-}"
    clean "${TERM_ID}" "TERM"
    WHEN="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    HOST="${AIRYMAX_HOST_ID:-$(hostname -s)}"
    clean "${HOST}" "host"
fi

has_word "${ARCH}" "${ARCHES}" || { err "未知架构: ${ARCH}"; exit "${TOOL_EINVAL}"; }
case "${COLS}" in ''|*[!0-9]*) err "--cols 必须是正整数"; exit "${TOOL_EINVAL}" ;; esac
case "${ROWS}" in ''|*[!0-9]*) err "--rows 必须是正整数"; exit "${TOOL_EINVAL}" ;; esac
[ "${COLS}" -gt 0 ] && [ "${ROWS}" -gt 0 ] || { err "终端尺寸必须为正"; exit "${TOOL_EINVAL}"; }

SHOT_ID=""
if [ -n "${SHOT}" ]; then
    [ -f "${SHOT}" ] || { err "截图不存在: ${SHOT}"; exit "${TOOL_EINVAL}"; }
    SHOT_ID="$(sha256sum "${SHOT}" | cut -d' ' -f1)"
fi

mkdir -p "${OUT}" || die "无法创建 ${OUT}"
DEST="${OUT}/${ARCH}-${LABEL}.field"
[ ! -e "${DEST}" ] || die "记录已存在: ${DEST}（同一格不允许覆盖）"

{
    printf 'AIRYMAX-TUI-FIELD/1\n'
    printf 'case=%s\n' "${LABEL}"
    printf 'evidence=%s\n' "${EVIDENCE}"
    printf 'arch=%s\n' "${ARCH}"
    printf 'os=%s\n' "${OS}"
    printf 'build=%s\n' "${BUILD_ID}"
    [ -z "${WHEN}" ] || printf 'when=%s\n' "${WHEN}"
    [ -z "${HOST}" ] || printf 'host=%s\n' "${HOST}"
    printf 'term=%s\n' "${TERM_ID}"
    printf 'cols=%s\n' "${COLS}"
    printf 'rows=%s\n' "${ROWS}"
    printf 'observed=%s\n' "${OBS}"
    printf 'result=%s\n' "${RESULT}"
    [ -z "${NOTE}" ] || printf 'note=%s\n' "${NOTE}"
    [ -z "${SHOT_ID}" ] || printf 'screenshot=%s\n' "${SHOT_ID}"
    printf 'AIRYMAX-TUI-FIELD-END\n'
} >"${DEST}.tmp" || die "写入失败: ${DEST}"

mv "${DEST}.tmp" "${DEST}" || die "落盘失败: ${DEST}"
say "已记录 ${LABEL} (${ARCH}) → ${DEST}"
