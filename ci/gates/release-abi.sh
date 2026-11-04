#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-abi.sh — syscall ABI 报告（发布物料 12）
#
# 用法: ci/gates/release-abi.sh report --ver VER [--root DIR] [--out DIR]
#
# 物料 12 是「本版不破坏用户空间 ABI」的可核对证据（09-release-process.md
# §3.1，铁律 OS-IRON-001）。报告不重新实现判定：判定由 tools/abi-check 做，
# 本门禁只负责三件事——跑一次真实检查、把判定与溯源钉进报告、失败就不出
# 报告。发布页上挂一份「检查失败但仍存在」的报告，比不挂更糟：它会被当成
# ABI 已核验的凭据。
#
# 报告正文是工具原始输出，只做一处改写：把仓库根前缀剥掉，路径一律相对化。
# 工具打印的是绝对路径，直接贴进发布物会把构建机的目录结构带上发布页，
# 也会让同一份源码在两台机器上产出不同字节的报告——报告本身就没法做摘要
# 与确定性比对了。
#
# 溯源行记录超项目提交、基线快照与契约快照的 sha256、以及由
# SOURCE_DATE_EPOCH 换算的生成时间：这三项决定「报告说的是哪棵树、哪份
# 基线」。时间取提交时间而非当前时间，同一提交重跑得到同一份报告。
#
# 退出码: 0 通过；1 检查失败或报告不可写；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly TOOL="tools/abi-check/abi_check.py"
readonly BASE="tools/abi-check/abi_baseline.json"
readonly SNAP="tools/abi-check/contract/syscall_registry.snapshot.json"
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
OUT="."
VER=""
RAW=""
epoch=0

cleanup()
{
    [ -z "${RAW}" ] || rm -f "${RAW}"
}

trap cleanup EXIT

usage()
{
    sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"
}

# rel —— 把工具输出里的仓库根前缀剥掉。用 index/substr 而不是 sed 正则：
# 根路径含 `.` 等正则元字符，转义漏一个就会静默不改写。
rel()
{
    awk -v r="${ROOT}/" '{
        while ((i = index($0, r)) > 0)
            $0 = substr($0, 1, i - 1) substr($0, i + length(r))
        print
    }' "$1"
}

sha()
{
    sha256sum "$1" | cut -d' ' -f1
}

stamp()
{
    date -u -d "@${epoch}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null ||
        die "无法把 SOURCE_DATE_EPOCH 换算成时间戳: ${epoch}"
}

# check —— 跑一次真实判定，要求总项数与通过项数相等。工具退出码为 0 但
# 报告写着 n/m 的残缺情况同样判负：判定行才是取件方读的那一行。
check()
{
    local rc

    python3 "${ROOT}/${TOOL}" --repo-root "${ROOT}" >"${RAW}" 2>&1
    rc=$?
    if [ "${rc}" -ne 0 ]; then
        tail -n 20 "${RAW}" >&2
        die "ABI 检查失败（rc=${rc}），不产出报告"
    fi
    if ! awk -F'[ /]+' '
        /^Checks: / {
            if ($2 == $3 && $5 == "0" && $6 == "failed") pass = 1
        }
        END { exit !pass }' "${RAW}"; then
        die "ABI 检查未给出全通过判定行: $(grep -m1 '^Checks: ' "${RAW}")"
    fi
}

report()
{
    local dest="$1" head summary

    [ -f "${ROOT}/${TOOL}" ] || die "缺少 ${TOOL}"
    [ -f "${ROOT}/${BASE}" ] || die "缺少 ${BASE}"
    [ -f "${ROOT}/${SNAP}" ] || die "缺少 ${SNAP}"
    head="$(git -C "${ROOT}" rev-parse HEAD)" ||
        die "无法取到超项目提交（报告失去溯源）"
    summary="$(grep -m1 '^Checks: ' "${RAW}")"

    mkdir -p "$(dirname "${dest}")" || die "无法创建输出目录: ${dest}"
    {
        printf '# AirymaxOS v%s syscall ABI 报告\n\n' "${VER}"
        printf -- '- 判定: PASS（%s）\n' "${summary#Checks: }"
        printf -- '- 超项目提交: %s\n' "${head}"
        printf -- '- 基线快照: %s sha256=%s\n' "${BASE}" "$(sha "${ROOT}/${BASE}")"
        printf -- '- 契约快照: %s sha256=%s\n' "${SNAP}" "$(sha "${ROOT}/${SNAP}")"
        printf -- '- 生成时间: %s\n' "$(stamp)"
        printf -- '- 权威依据: docs-linux 140-application-development/07-syscall-registry.md'
        printf '（OS-IRON-001）\n\n'
        printf '工具输出的 Sources 段逐行标明每个来源实际取自哪份文件：文档 SSoT\n'
        printf '未随检出时，工具以派生的契约快照为来源并在该段体现，本报告不自作判断。\n\n'
        printf '## 检查输出\n\n```text\n'
        rel "${RAW}"
        printf '```\n'
    } >"${dest}" || die "写入报告失败: ${dest}"
}

cmd="${1:-}"
[ "$#" -gt 0 ] || { usage >&2; exit "${EINVAL}"; }
shift

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ver)     VER="${2:-}"; shift 2 ;;
        --root)    ROOT="${2:-}"; shift 2 ;;
        --out)     OUT="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

case "${cmd}" in
    report)
        [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
        need_cmd python3 git sha256sum date awk
        epoch="${SOURCE_DATE_EPOCH:-0}"
        case "${epoch}" in
            ''|*[!0-9]*) die "SOURCE_DATE_EPOCH 须为非负整数: ${epoch}" ;;
        esac
        RAW="$(mktemp)"
        group "Syscall ABI report"
        check || exit 1
        report "${OUT}/agent-linux-${VER}-abi-report.md" || exit 1
        say "agent-linux-${VER}-abi-report.md $(wc -c <"${OUT}/agent-linux-${VER}-abi-report.md") 字节"
        endgroup
        ;;
    -h|--help)
        usage
        ;;
    *)
        err "未知子命令: ${cmd}"
        usage >&2
        exit "${EINVAL}"
        ;;
esac
