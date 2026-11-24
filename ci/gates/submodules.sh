#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/submodules.sh — 只取本轮真正会被读到的子模块
#
# 用法: ci/gates/submodules.sh [--assemble]
#
# 递归 checkout 会把九个子模块全部拉下来，其中 kernel 一棵树就有 1.5G。
# 默认集合是 distro 与 system：装机腿、通道回写与门禁脚本的读点都落在
# 这两棵树上；memory / security / cognition / services / cloudnative /
# tests-linux 在这些路径上没有读点。这些下载不影响产物，只把每一轮的
# 固定开销抬高几分钟，而固定开销正是 assemble 超时的主要成分（W-1）。
#
# --assemble 是组装段的读点集合：distro/specs 下的配方以 source-root 与
# source-bundle 引用 services 与 kernel/include（系统包要编译守护进程，
# 并以 UAPI 头作为编译契约），组装因此必须同时取到这四棵子树。集合只由
# 配方声明决定，不随「是否复用 Release 内核包」变化——UAPI 头是编译输入，
# 与内核包从哪里来无关；曾按后者裁剪，缺件直到 RPM 阶段才暴露。
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

assemble=0
for arg in "$@"; do
    case "${arg}" in
        --assemble) assemble=1 ;;
        *) die "未知参数: ${arg}（可用: --assemble）" ;;
    esac
done

subs=(distro system)
if [ "${assemble}" -eq 1 ]; then
    subs+=(services kernel)
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
