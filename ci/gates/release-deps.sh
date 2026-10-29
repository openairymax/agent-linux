#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-deps.sh — 内核四腿构建所需的宿主工具链
#
# 用法: ci/ci.sh release-deps
#
# 同一段安装在暖缓存 runner 上 33s 完成，冷启动 28min，也出现过整步卡住
# 45min 以上的实例。acquire 超时、重试与硬超时统一收在 ci/lib/apt.sh，
# 本 gate 只声明包与断言，让镜像故障表现为一次可重试的失败。
#
# 断言放在装包之后、构建之前：交叉 strip 与 clang 缺席时，rpmspec 要到
# %install 或 tools 子包的 bpftool skeleton 阶段才报错，代价是几十分钟。
#
# 退出码: 0 通过；1 安装失败或缺工具；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly NATIVE_DEPS=(
    flex bison libelf-dev libssl-dev libdw-dev libtraceevent-dev
    libcap-dev libnuma-dev systemtap-sdt-dev python3-dev zlib1g-dev
    bc cpio dwarves rsync rpm clang llvm
)
readonly CROSS_GCC=(
    gcc-aarch64-linux-gnu gcc-riscv64-linux-gnu gcc-13-loongarch64-linux-gnu
)
# 交叉腿的 brp-strip 以 <prefix>strip 去符号，缺任一即无法打包。
readonly CROSS_STRIP=(
    aarch64-linux-gnu-strip riscv64-linux-gnu-strip loongarch64-linux-gnu-strip
)

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/apt.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/apt.sh"

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) echo "用法: ci/ci.sh release-deps"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

apt_sync || die "apt update 失败"
apt_install "${NATIVE_DEPS[@]}" || die "宿主依赖安装失败"

# tools 子包的 bpftool skeleton 依赖 clang 与 llvm-strip。
need_cmd clang llvm-strip

apt_install "${CROSS_GCC[@]}" || die "交叉工具链安装失败"
# noble 只提供带版本号的 loongarch64 交叉 gcc，补齐 CROSS_COMPILE 期望的
# 无版本号驱动名。
sudo ln -sf "$(command -v loongarch64-linux-gnu-gcc-13)" \
    /usr/local/bin/loongarch64-linux-gnu-gcc

need_cmd "${CROSS_STRIP[@]}"
say "内核构建工具链就绪（${#NATIVE_DEPS[@]} 个宿主包, 3 套交叉工具链）"
