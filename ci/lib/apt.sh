#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/apt.sh — 流水线与门禁安装宿主依赖的唯一入口
#
# release、ci-airy 与两条门禁原先各自写 apt-get，参数互不相同，且都没有
# acquire 超时：镜像源不响应时 apt 会一直等下去，一步就能把 240min 的 job
# 预算烧光（v0.0.3 的发布就曾停在这一步 45min 以上）。这里统一四件事：
# acquire 超时与重试、非交互、整条命令的硬超时、root 判定，调用方只写包名。
#
# 用法: . ci/lib/apt.sh; apt_sync; apt_install curl git; apt_install_min qemu-utils
# 可调: APT_TIMEOUT（整条命令的硬超时，默认 1200s）
# ===========================================================================

: "${APT_TIMEOUT:=1200}"
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
    if ! timeout "${APT_TIMEOUT}" "${APT_SUDO[@]}" \
        apt-get "${APT_OPTS[@]}" "$@"; then
        _apt_note "apt-get $1 失败或超过 ${APT_TIMEOUT}s，请检查镜像源与网络"
        return 1
    fi
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
