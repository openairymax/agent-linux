#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-kernel.sh — 四架构内核 RPM 构建与包矩阵核对
#
# 用法: ci/ci.sh release-kernel build
#       ci/ci.sh release-kernel matrix
#
# 单 job 内顺序构建（管理仓 2 job 铁律，不得引入 matrix job）。与 ISO 路径
# 共用 distro/scripts/build-kernel.sh 与同一份配方：源码归档、三步配置、
# 包矩阵只有一处事实源，Release 与 ISO 因此不可能产出哈希不同的同名包。
#
# 产物先落在 RUNNER_TEMP（源码区之外），确认后拷入 dist/。实测 x86_64
# 60.6min、aarch64 57.5min、riscv64 16.7min，末腿 loongarch64 仍需约
# 75min——封顶由 workflow 的 timeout-minutes 承担，本 gate 不设自造上限。
#
# matrix 按 190-distribution/01-rpm-packaging.md §3.1 核对：tools 子包只在
# 目标架构与构建机一致时产出，交叉腿必须恰好没有它。
#
# 退出码: 0 通过；1 构建或矩阵不符；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ARCHES=(x86_64 aarch64 riscv64 loongarch64)
readonly LEGS=(
    'x86_64|'
    'aarch64|aarch64-linux-gnu-'
    'riscv64|riscv64-linux-gnu-'
    'loongarch64|loongarch64-linux-gnu-'
)
declare -A WANT=(
    [x86_64]="core headers devel modules tools"
    [aarch64]="core headers devel modules"
    [riscv64]="core headers devel modules"
    [loongarch64]="core headers devel modules"
)

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

build()
{
    local leg arch cross out
    local -a args

    need_cmd rpmbuild cp
    for leg in "${LEGS[@]}"; do
        arch="${leg%%|*}"
        cross="${leg#*|}"
        group "kernel RPM ${arch}"
        out="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/kernel-${arch}"
        args=(--out "${out}" --kernel "${GITHUB_WORKSPACE:-$(pwd)}/kernel"
              --arch "${arch}" --nodeps)
        [ -z "${cross}" ] || args+=(--cross "${cross}")
        distro/scripts/build-kernel.sh "${args[@]}" || die "内核构建失败: ${arch}"
        mkdir -p "dist/rpm/${arch}"
        cp "${out}/RPMS/${arch}/"*.rpm "dist/rpm/${arch}/" ||
            die "未产出 ${arch} RPM"
        ls -l "dist/rpm/${arch}"
        endgroup
    done
    say "四架构内核 RPM 已就绪"
}

matrix()
{
    local arch got expect sub pkg have want total=0

    need_cmd rpm
    for arch in "${ARCHES[@]}"; do
        set -- dist/rpm/"${arch}"/*.rpm
        [ -f "$1" ] || die "${arch} 未产出任何 RPM"
        got="$(rpm -qp --queryformat '%{NAME} %{ARCH}\n' "$@" | sort -u)"
        want="${WANT[${arch}]}"
        expect="airymaxos-kernel"
        for sub in ${want}; do
            expect="${expect} airymaxos-kernel-${sub}"
        done
        for pkg in ${expect}; do
            printf '%s\n' "${got}" | grep -qx "${pkg} ${arch}" ||
                die "${arch} 缺少 ${pkg}"
        done
        set -- ${expect}
        have="$(printf '%s\n' "${got}" | grep -c . || true)"
        [ "${have}" -eq "$#" ] || die "${arch} 包数 ${have}，期望 $#"
        say "${arch}: $(printf '%s' "${got}" | tr '\n' ' ')"
        total=$((total + have))
    done
    say "内核 RPM 总数: ${total}"
}

case "${1:-}" in
    build)  build ;;
    matrix) matrix ;;
    -h|--help) echo "用法: ci/ci.sh release-kernel <build|matrix>"; exit 0 ;;
    *) echo "用法: ci/ci.sh release-kernel <build|matrix>" >&2; exit "${EINVAL}" ;;
esac
