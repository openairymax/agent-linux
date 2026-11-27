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
# source-bundle 声明会被归档成源码包的子树（系统包要编译守护进程，内核以
# UAPI 头作为编译契约，另有安全、云原生、认知三棵源码树随系统包发行）。
# 集合由配方声明推导：先取 distro，再从声明里取出全部顶层子树一并取齐，
# 不随「是否复用 Release 内核包」变化——UAPI 头是编译输入，与内核包从
# 哪里来无关。手工清单曾落后于配方，新增子树到 RPM 阶段才以缺件暴露。
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
cd "${ROOT}"

if [ "${assemble}" -eq 1 ]; then
    git submodule update --init --depth 1 -- distro
    mapfile -t declared < <(
        grep -rhoE '(source-root|source-bundle)[[:space:]]+\.\./\.\./[A-Za-z0-9._/-]+' distro/specs |
            awk '{print $2}' | sed 's#^\.\./\.\./##' | cut -d/ -f1 | sort -u
    )
    [ "${#declared[@]}" -gt 0 ] || die "distro/specs 未声明任何源码子树"
    sub_paths=$(git config -f .gitmodules --get-regexp '\.path$' | awk '{print $2}')
    for top in "${declared[@]}"; do
        if printf '%s\n' "${sub_paths}" | grep -qx "${top}"; then
            subs+=("${top}")
        elif [ ! -d "${top}" ]; then
            die "配方引用的源码树不在树内: ${top}"
        fi
    done
    mapfile -t subs < <(printf '%s\n' "${subs[@]}" | awk '!seen[$0]++')
fi

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
