#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-build.sh — Kbuild 矩阵单格编译
#
# 用法:
#   ci/gates/kernel-build.sh --config CONFIG --arch ARCH --compiler gcc|clang
#                            [--src DIR] [--log FILE]
#
# 一格 = (config, arch, compiler)。整张 28 格矩阵由 ci-kernel.yml 的
# matrix 展开，本脚本只负责「给一格参数，把这一格编出来并判定红绿」。
# 这样矩阵里任何一格失败都能在本地用同一条命令复现，而不必触发整条云端
# 流水线。
#
# 判定口径（与 03-ci-cd-pipeline.md 一致）:
#   1. 出现 `error:` 即失败；
#   2. `warning:` 只对 Airymax 自有路径（security/airy、kernel/{superv,
#      syscalls,corekern,log,ipc}、/airymax/）零容忍，上游告警仅报告；
#   3. allmodconfig 只编内建面（vmlinux），模块面下沉 nightly——理由是
#      全量模块在标准运行器上超过 OS-STD-TEST-011 的 60 分钟上限。
#
# 退出码: 0 通过；1 编译或告警判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=kernel-build
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
CONFIG=""
ARCH=""
COMPILER=""
LOG="build.log"

# Airymax 自有代码路径：这些路径上的告警一律阻断。
readonly AIRY_RE='security/airy/|kernel/(superv|syscalls|corekern|log|ipc)/|/airymax/'

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-build.sh --config CONFIG --arch ARCH --compiler gcc|clang
                               [--src DIR] [--log FILE]

  --config    defconfig | allnoconfig | allmodconfig | airy_defconfig
  --arch      x86_64 | arm64 | riscv | loongarch
  --compiler  gcc | clang
  --src       内核源码目录（缺省 <repo>/kernel）
  --log       编译日志路径（缺省 build.log，相对当前目录）
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --compiler) COMPILER="${2:-}"; shift 2 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --log) LOG="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

[ -n "${CONFIG}" ] || { usage >&2; exit "${EINVAL}"; }
[ -n "${ARCH}" ] || { usage >&2; exit "${EINVAL}"; }
[ -n "${COMPILER}" ] || { usage >&2; exit "${EINVAL}"; }

case "${CONFIG}" in defconfig | allnoconfig | allmodconfig | airy_defconfig) ;;
*)
    note "不支持的 config: ${CONFIG}"
    exit "${EINVAL}"
    ;;
esac
case "${ARCH}" in x86_64 | arm64 | riscv | loongarch) ;;
*)
    note "不支持的架构: ${ARCH}"
    exit "${EINVAL}"
    ;;
esac
case "${COMPILER}" in gcc | clang) ;;
*)
    note "不支持的编译器: ${COMPILER}"
    exit "${EINVAL}"
    ;;
esac

# clang 与 loongarch 的组合上游尚不支持，矩阵已排除，这里挡住手工误调。
if [ "${ARCH}" = loongarch ] && [ "${COMPILER}" = clang ]; then
    note "上游 clang 不支持 loongarch，该组合不在矩阵内"
    exit "${EINVAL}"
fi

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"

kdir_of()
{
    case "${1}" in
    x86_64) echo x86 ;;
    arm64) echo arm64 ;;
    riscv) echo riscv ;;
    loongarch) echo loongarch ;;
    esac
}

cross_of()
{
    case "${1}" in
    x86_64) echo "" ;;
    arm64) echo aarch64-linux-gnu- ;;
    riscv) echo riscv64-linux-gnu- ;;
    loongarch) echo loongarch64-linux-gnu- ;;
    esac
}

kdir="$(kdir_of "${ARCH}")"

if [ "${COMPILER}" = clang ]; then
    # LLVM=1 让 clang/lld/llvm 提供全部工具，跨架构目标由 ARCH 推导；
    # 不能再设 CROSS_COMPILE，否则会去调用并不存在的 GNU 交叉 binutils。
    mkargs=(LLVM=1)
else
    mkargs=("CROSS_COMPILE=$(cross_of "${ARCH}")")
fi

: >"${LOG}"
say "Kbuild ${CONFIG} / ${ARCH} / ${COMPILER}（日志 ${LOG}）"

cd "${SRC}" || die "无法进入 ${SRC}"

if [ "${CONFIG}" = airy_defconfig ]; then
    # airy_defconfig 是叠加在架构 base defconfig 之上的配置片段，不是 make
    # 目标，必须走 merge_config.sh 两步法。
    make ARCH="${ARCH}" "${mkargs[@]}" defconfig >>"${LOG}" 2>&1 ||
        die "defconfig 阶段失败"
    ./scripts/kconfig/merge_config.sh -m .config \
        "arch/${kdir}/configs/airy_defconfig" >>"${LOG}" 2>&1 ||
        die "merge_config 阶段失败"
    make ARCH="${ARCH}" "${mkargs[@]}" olddefconfig >>"${LOG}" 2>&1 ||
        die "olddefconfig 阶段失败"
else
    make ARCH="${ARCH}" "${mkargs[@]}" "${CONFIG}" >>"${LOG}" 2>&1 ||
        die "${CONFIG} 求解失败"
fi

if [ "${CONFIG}" = allmodconfig ]; then
    # 全量模块编译超出单 job 60 分钟上限，PR 门禁只覆盖内建面（vmlinux），
    # 仍能抓到 #ifdef 错配与内建符号冲突；模块级符号冲突与循环依赖归 nightly。
    say "allmodconfig 只编 vmlinux（模块面归 nightly）"
    make ARCH="${ARCH}" "${mkargs[@]}" vmlinux -j"$(nproc)" >>"${LOG}" 2>&1 ||
        die "vmlinux 编译失败"
else
    make ARCH="${ARCH}" "${mkargs[@]}" -j"$(nproc)" >>"${LOG}" 2>&1 ||
        die "内核编译失败"
    make ARCH="${ARCH}" "${mkargs[@]}" modules -j"$(nproc)" >>"${LOG}" 2>&1 ||
        die "模块编译失败"
fi

if grep -E 'error:' "${LOG}" >/dev/null 2>&1; then
    grep -E 'error:' "${LOG}" | head -n 40 >&2
    die "编译日志出现 error:"
fi

if grep -E 'warning:' "${LOG}" >warnings.log 2>/dev/null; then
    if grep -E "${AIRY_RE}" warnings.log >/dev/null 2>&1; then
        grep -E "${AIRY_RE}" warnings.log | head -n 40 >&2
        die "Airymax 自有代码出现编译告警"
    fi
    note "存在上游告警，Airymax 路径干净"
else
    say "无编译告警"
fi

say "通过：${CONFIG} / ${ARCH} / ${COMPILER}"
