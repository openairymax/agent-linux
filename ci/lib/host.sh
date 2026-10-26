#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/host.sh — 宿主环境准备原语
#
# 这些动作此前以多行 shell 的形式复制在 image-build / kernel-verify / airy-verify /
# release 四个 workflow 里，改一处要改四处，且本地无法执行也就无法验证。
# 收敛到此处后，YAML 只需一行调用。
# ===========================================================================

# 托管 runner 预装的工具链（dotnet/android/ghc/CodeQL/boost/ghcup/swift）
# 最多占 30G；镜像流水线要落一个 4G ISO 加两 G 载荷，不清理就在组装尾段
# ENOSPC，而且失败点在 ISO 生成之后，前面十几分钟的编译全白费。
#
# 只在 GITHUB_ACTIONS=true 时执行：这些路径属于宿主自己的安装，本地开发者
# 不该因为跑一次门禁就被删掉 /usr/share/dotnet。
free_disk()
{
    if [ "${GITHUB_ACTIONS:-}" != true ]; then
        note "非 CI 环境，跳过 runner 磁盘清理"
        return 0
    fi
    group "free runner disk"
    sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc \
        /opt/hostedtoolcache/CodeQL /usr/local/share/boost \
        /usr/local/.ghcup /usr/share/swift
    df -h /
    endgroup
}

need_cmd()
{
    local c missing=()
    for c in "$@"; do
        command -v "${c}" >/dev/null 2>&1 || missing+=("${c}")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        die "缺少命令: ${missing[*]}"
    fi
}

# 托管 runner 的 /dev/kvm 存在但默认只有 root 可读写。可写时直接使用，
# 不可写再尝试 chmod；两处都失败不算错误——install-test.sh 会自行回退
# TCG，只是耗时上升。
enable_kvm()
{
    if [ ! -e /dev/kvm ]; then
        warn "/dev/kvm 不可用，回退 TCG（耗时显著上升）"
        return 0
    fi
    if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
        say "KVM 可用（权限已满足）"
    elif sudo -n chmod 666 /dev/kvm 2>/dev/null; then
        say "KVM 可用（已放开权限）"
    else
        warn "/dev/kvm 存在但不可授权，回退 TCG"
    fi
}
