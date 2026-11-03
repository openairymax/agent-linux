#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/submodules.sh — 只取本轮真正会被读到的子模块
#
# 用法: ci/gates/submodules.sh [--need-kernel]
#
# 递归 checkout 会把九个子模块全部拉下来，其中 kernel 一棵树就有 1.5G，
# 而组装段只在「没有从 Release 复用内核包」时才读它；memory / security /
# cognition / services / cloudnative / tests-linux 在组装链里没有任何读点
# （镜像载荷只由 distro/specs 的三个配方产出，Source 全部落在 distro 与
# system）。这些下载不影响产物，只把每一轮的固定开销抬高几分钟，而
# 固定开销正是 assemble 超时的主要成分（W-1）。
#
# 判据按「谁读它」而不是按体积：子树一旦被某条组装路径读到就必须在这里
# 出现，否则产物会静默缺件——default_srcs() 对缺失目录是跳过而非报错。
# 因此初始化后逐个校验目录非空，把「漏列」变成起步即失败。
#
# --depth 1 只取 gitlink 指向的那一个提交：build-kernel.sh 归档用的是
# git archive HEAD 与提交时间，浅克隆足够，历史对它没有意义。
#
# 退出码: 0 全部就绪；2 参数错误；其他为 git 的失败码。
# ===========================================================================
set -euo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

need_kernel=0
for arg in "$@"; do
    case "${arg}" in
        --need-kernel) need_kernel=1 ;;
        *) die "未知参数: ${arg}（可用: --need-kernel）" ;;
    esac
done

subs=(distro system)
if [ "${need_kernel}" -eq 1 ]; then
    subs+=(kernel)
fi

cd "${ROOT}"

group "checkout submodules: ${subs[*]}"
git submodule update --init --depth 1 -- "${subs[@]}"

empty=()
for sub in "${subs[@]}"; do
    if [ -z "$(ls -A "${sub}" 2>/dev/null)" ]; then
        empty+=("${sub}")
    fi
done
[ "${#empty[@]}" -eq 0 ] || die "子模块未取到内容: ${empty[*]}"
endgroup
