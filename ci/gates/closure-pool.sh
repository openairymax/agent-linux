#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/closure-pool.sh — 在宿主收割安装闭包池（播种机入口）
#
# 用法: ci/gates/closure-pool.sh --out DIR [--arch ARCH] [--skip GLOB]
#                                [--ks FILE]
#
# 为什么要有这条路径：安装闭包约 2.5 GB，CI 跑在托管运行商的出口上，
# 到国内镜像实测只有 16–22 KiB/s，180 分钟的组装预算下不完，image-build
# 因此连续超时；而一台国内验证机到清华/华为镜像实测 6–7 MB/s，几分钟
# 就能收完。于是把「收割」从「组装」里拆出来：宿主收割 → 经 ghcr.io
# 交付 → CI 池空时注入（ci/gates/pool-seed.sh）。
#
# 必须在宿主跑而不是套容器：播种机自带 dnf/createrepo_c，容器只用来复现
# 组装基线，套进来反而要重新准备闭包源。求解与收割逻辑全在
# distro/scripts/build-desktop.sh --closure-only 里，本 gate 只做参数校验
# 与转发，不复制第二份实现。
#
# 播种机通常没有自建 RPM：配方点名的 airymaxos-kernel、airymaxos-system、
# airymaxos-release 等远端并不存在，在线求解必然失败。--skip 缺省排除
# airymax*，让事务在不含自有包的前提下成立；排除项下的事务不完整，因此
# 这里强制 CLOSURE_LOCK_WRITE=0，闭包清单仍只由 CI 写。
#
# 产物：DIR/payload/airymax/Packages/*.rpm 与 repodata/；打包与推送步骤见
# ci/gates/pool-seed.sh 的头注释。
#
# 退出码: 0 成功；5 参数不满足；其余透传 build-desktop.sh。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly DRIVER="${ROOT}/distro/scripts/build-desktop.sh"
readonly KS_DEFAULT="${ROOT}/distro/kickstart/airymaxos-workstation.ks"

OUT=""
ARCH="x86_64"
KS="${KS_DEFAULT}"
SKIPS=("airymax*")

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)  OUT="${2:-}"; shift 2 ;;
        --arch) ARCH="${2:-}"; shift 2 ;;
        --ks)   KS="${2:-}"; shift 2 ;;
        --skip) SKIPS+=("${2:-}"); shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/closure-pool.sh --out DIR [--arch ARCH] [--skip GLOB] [--ks FILE]"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${OUT}" ] || { err "--out 必填"; exit "${EINVAL}"; }
[ -f "${DRIVER}" ] || die "缺少构建驱动: ${DRIVER}"
[ -f "${KS}" ] || die "kickstart 不存在: ${KS}"
need_cmd dnf createrepo_c python3

args=(--closure-only --out "${OUT}" --arch "${ARCH}" --ks "${KS}")
for glob in "${SKIPS[@]}"; do
    args+=(--closure-skip "$glob")
done

say "宿主收割闭包池: ${OUT}（${ARCH}）"
CLOSURE_LOCK_WRITE=0 bash "${DRIVER}" "${args[@]}"
