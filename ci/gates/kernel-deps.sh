#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-deps.sh — 安装内核构建/校验所需的工具链
#
# 用法:
#   ci/gates/kernel-deps.sh --kind build|verify [--arch ARCH] [--compiler gcc|clang]
#
# 这条门禁此前是 ci-kernel.yml 里的两段内联 shell，一段给 Kbuild 矩阵装
# 交叉工具链，一段给静态分析装 sparse/coccinelle/virtme-ng。两段内容互相
# 重叠（flex/bison/libelf 两边都要），改一处忘一处就会出现「矩阵能编、
# verify 编不了」的错位。
#
# 宿主差异处理：托管 runner 是 Debian 系，直接用 apt 安装；其它发行版
# （例如现场的 openEuler）只做存在性校验——能跑就等于依赖齐，不能跑就
# 明确报缺哪个包，而不是在这里猜包管理器。
#
# 退出码: 0 依赖就绪；1 依赖缺失；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=kernel-deps
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

KIND=""
ARCH=x86_64
COMPILER=gcc

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-deps.sh --kind build|verify [--arch ARCH] [--compiler gcc|clang]

  --kind build     Kbuild 矩阵：基础构建依赖 + 目标架构交叉工具链
  --kind verify    静态分析与运行时测试：sparse/coccinelle/virtme-ng/qemu
  --arch ARCH      build 用途，x86_64|arm64|riscv|loongarch（缺省 x86_64）
  --compiler C     build 用途，gcc|clang（缺省 gcc）
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --kind) KIND="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --compiler) COMPILER="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

case "${KIND}" in
build | verify) ;;
*)
    note "--kind 必须是 build 或 verify"
    usage >&2
    exit "${EINVAL}"
    ;;
esac

case "${ARCH}" in
x86_64 | arm64 | riscv | loongarch) ;;
*)
    note "不支持的架构: ${ARCH}"
    exit "${EINVAL}"
    ;;
esac

case "${COMPILER}" in
gcc | clang) ;;
*)
    note "不支持的编译器: ${COMPILER}"
    exit "${EINVAL}"
    ;;
esac

# 基础依赖：两条腿共用的那部分。
readonly BASE_PKGS="flex bison libelf-dev libssl-dev bc cpio dwarves"
readonly VERIFY_PKGS="sparse coccinelle rsync zstd file kmod iproute2 kbd
python3-yaml virtme-ng qemu-system-x86 busybox-static"

# 交叉编译器：只有 gcc 腿需要；clang 腿由 LLVM=1 自带跨架构能力。
cross_pkg()
{
    case "${ARCH}" in
    x86_64) echo "" ;;
    arm64) echo "gcc-aarch64-linux-gnu" ;;
    riscv) echo "gcc-riscv64-linux-gnu" ;;
    loongarch) echo "gcc-13-loongarch64-linux-gnu" ;;
    esac
}

cross_prefix()
{
    case "${ARCH}" in
    x86_64) echo "" ;;
    arm64) echo "aarch64-linux-gnu-" ;;
    riscv) echo "riscv64-linux-gnu-" ;;
    loongarch) echo "loongarch64-linux-gnu-" ;;
    esac
}

# 存在性校验所需的命令清单，两边共用，避免「装完发现还缺一个」。
need_cmds()
{
    local list="make gcc"
    case "${KIND}" in
    build)
        list="${list} flex bison bc cpio"
        if [ "${COMPILER}" = clang ]; then
            list="${list} clang ld.lld"
        else
            local prefix
            prefix="$(cross_prefix)"
            [ -n "${prefix}" ] && list="${list} ${prefix}gcc"
        fi
        ;;
    verify)
        list="${list} sparse spatch rsync zstd file kmod vng qemu-system-x86_64 busybox"
        ;;
    esac
    printf '%s\n' ${list}
}

missing_cmds()
{
    local c out=""
    for c in $(need_cmds); do
        command -v "${c}" >/dev/null 2>&1 || out="${out} ${c}"
    done
    printf '%s' "${out# }"
}

install_debian()
{
    local pkgs="${BASE_PKGS}"
    case "${KIND}" in
    build)
        if [ "${COMPILER}" = clang ]; then
            pkgs="${pkgs} clang lld llvm"
        else
            local cp
            cp="$(cross_pkg)"
            [ -n "${cp}" ] && pkgs="${pkgs} ${cp}"
        fi
        ;;
    verify)
        pkgs="${pkgs} ${VERIFY_PKGS}"
        ;;
    esac
    group "apt-get install (${KIND})"
    # shellcheck disable=SC2086  # 包名按空格拆分是本函数的接口约定
    ${SUDO} apt-get update -qq
    # shellcheck disable=SC2086
    ${SUDO} apt-get install -y ${pkgs}
    endgroup
}

# noble 只提供带版本号的 loongarch64 交叉 gcc，而 CROSS_COMPILE 期望无版本
# 号的驱动名。这个别名只能在装完之后补，所以放在安装路径之后统一处理。
link_loongarch_alias()
{
    [ "${KIND}" = build ] || return 0
    [ "${COMPILER}" = gcc ] || return 0
    [ "${ARCH}" = loongarch ] || return 0
    command -v loongarch64-linux-gnu-gcc >/dev/null 2>&1 && return 0
    local src
    src="$(command -v loongarch64-linux-gnu-gcc-13 || true)"
    [ -n "${src}" ] || return 0
    ${SUDO} ln -sf "${src}" /usr/local/bin/loongarch64-linux-gnu-gcc
    note "补齐 loongarch64-linux-gnu-gcc 别名"
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

if command -v apt-get >/dev/null 2>&1; then
    install_debian
    link_loongarch_alias
    miss="$(missing_cmds)"
    if [ -n "${miss}" ]; then
        note "安装后仍缺命令: ${miss}"
        exit 1
    fi
    say "工具链就绪（kind=${KIND} arch=${ARCH} compiler=${COMPILER}）"
    exit 0
fi

# 非 Debian 宿主：只校验存在性，让同一条门禁在 RPM 系现场机上也能给出
# 明确结论，而不是报「没有 apt-get」。
miss="$(missing_cmds)"
if [ -n "${miss}" ]; then
    note "非 Debian 宿主且缺少命令: ${miss}（请手工安装对应软件包）"
    exit 1
fi
say "工具链已存在（kind=${KIND} arch=${ARCH} compiler=${COMPILER}），跳过安装"
