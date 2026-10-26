#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/apt.sh — 流水线与门禁安装宿主依赖的唯一入口
#
# release、airy-verify 与两条门禁原先各自写 apt-get，参数互不相同，且都没有
# acquire 超时：镜像源不响应时 apt 会一直等下去，一步就能把 240min 的 job
# 预算烧光（v0.0.3 的发布就曾停在这一步 45min 以上）。这里统一四件事：
# acquire 超时与重试、非交互、整条命令的硬超时、root 判定，调用方只写包名。
#
# 用法: . ci/lib/apt.sh; apt_sync; apt_install curl git; apt_install_min qemu-utils
# 可调: APT_TIMEOUT（单次尝试的硬超时，默认 900s）、APT_TRIES（尝试次数，默认 3）
#
# kernel-verify 矩阵 8 并发同时从托管镜像拉交叉工具链大包（gcc-13-* 单包
# 20MB+），单格带宽会被挤兑到几十 KB/s，单次尝试经常在 1200s 预算内拿不
# 下整包。apt 的 partial 缓存在 Acquire::Retries 与进程级重试之间都保留，
# 所以每次重试都从上次断点续传，总进度单调前进；硬超时只约束单次尝试。
# ========================================================================

: "${APT_TIMEOUT:=900}"
: "${APT_TRIES:=3}"
APT_OPTS=(-o Acquire::Retries=3 -o Acquire::http::Timeout=30
          -o Acquire::https::Timeout=30)

if [ "$(id -u)" -eq 0 ]; then
    APT_SUDO=()
else
    APT_SUDO=(sudo)
fi
# 托管 runner 没有 tty，debconf 的交互提问会让安装永久挂起。
export DEBIAN_FRONTEND=noninteractive

_apt_note()
{
    if declare -F note >/dev/null 2>&1; then
        note "$*"
    else
        printf '%s\n' "$*" >&2
    fi
}

_apt_run()
{
    local attempt=1
    while [ "${attempt}" -le "${APT_TRIES}" ]; do
        if timeout "${APT_TIMEOUT}" "${APT_SUDO[@]}" \
            apt-get "${APT_OPTS[@]}" "$@"; then
            return 0
        fi
        _apt_note "apt-get $1 第 ${attempt}/${APT_TRIES} 次尝试失败或超过 ${APT_TIMEOUT}s（partial 缓存保留，重试续传）"
        attempt=$((attempt + 1))
    done
    _apt_note "apt-get $1 在 ${APT_TRIES} 次尝试后仍失败，请检查镜像源与网络"
    return 1
}

apt_sync()
{
    _apt_run update -qq
}

apt_install()
{
    [ "$#" -gt 0 ] || return 0
    # shellcheck disable=SC2086  # APT_INSTALL_OPTS 是空白分隔的选项串
    _apt_run install -y ${APT_INSTALL_OPTS:-} "$@"
}

# 门禁的运行时依赖（qemu 等）不需要文档与推荐包，装机镜像保持精简。
apt_install_min()
{
    APT_INSTALL_OPTS=--no-install-recommends apt_install "$@"
}
