#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/pool-state.sh — 闭包缓存池的内容摘要
#
# 用法: ci/gates/pool-state.sh --dir DIR
#
# 缓存键必须随内容变化，否则两种坏结局各占一端：固定键在首轮中断后被永久
# 固化，半份池再也长不大；纯 run_id 键则每轮落一份完整副本，十 GiB 配额里
# 只装得下三四轮，且 restore 前缀永远命中最新一条——那条若是失败轮次留下
# 的空目录，池就永远从零开始（run 41056762297 之后的每一轮都是这个形态）。
#
# 按内容摘要取键，同一份池只存一次，池长大才产生新条目；restore 按前缀
# 命中最近一份，池单调增长时最近一份就是最完整的一份。
#
# 摘要取「文件名 + 字节数」而非内容哈希：2.5 GB 全量读一遍在 runner 上要
# 数分钟，而 RPM 文件名即 NEVRA，同名同长只在内容被替换时才成立；闭包
# 清单的 sha256 校验会在求解之前把这类包拦下，不会带进载荷。
#
# 输出: 设置 GITHUB_OUTPUT 时追加 count=<包数> 与 digest=<摘要>；否则
#       打印到标准输出。空池的 digest 为空串，调用方据此跳过保存。
#
# 退出码: 0 正常；5 参数不满足。
# ===========================================================================
set -uo pipefail

EINVAL=5
DIR=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir) DIR="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/pool-state.sh --dir DIR"
            exit 0 ;;
        *) printf 'error: 未知参数: %s\n' "$1" >&2; exit "${EINVAL}" ;;
    esac
done

[ -n "${DIR}" ] || { printf 'error: --dir 必填\n' >&2; exit "${EINVAL}"; }

count_of()
{
    find "${DIR}" -type f -name '*.rpm' 2>/dev/null | wc -l | tr -d ' '
}

digest_of()
{
    # 完整度进摘要：只按 RPM 取名会让「半份池补齐成完整池」落回同一个键，
    # 保存时撞上已有条目直接报错，完成标记因此永远存不下来，下一轮又把
    # 2.3 GB 制品重拉一遍。complete 位一翻，键就是新的。
    local complete=0

    [ -f "${DIR}/.seed-complete" ] && complete=1
    {
        printf 'complete=%s\n' "${complete}"
        find "${DIR}" -type f -name '*.rpm' -printf '%f %s\n' 2>/dev/null
    } | LC_ALL=C sort | sha256sum | cut -d' ' -f1
}

count="$(count_of)"
digest=""
[ "${count}" -gt 0 ] && digest="$(digest_of)"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf 'count=%s\ndigest=%s\n' "${count}" "${digest}" >>"${GITHUB_OUTPUT}"
else
    printf 'count=%s digest=%s\n' "${count}" "${digest}"
fi
