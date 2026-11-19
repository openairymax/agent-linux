#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/c-verify.sh — 用户态 C 组件（cloudnative / services）的构建与冒烟门禁
#
# 用法:
#   ci/gates/c-verify.sh --module cloudnative|services
#
# 两个模块长期没有进入任何流水线：services 只在本地被手工 `cmake --build`
# 过，cloudnative 则因为 include 根把内核内部头排在 libc 之前而一直编不过
# （`blkcnt_t` 冲突），却在 OS-CHK 的静态检查里显示全绿。构建面的失真比
# 静态检查漏报更危险——它让「能跑」失去判据，因此这里用真实编译补上。
#
# 判据分两步，缺一不可：
#   1. 出树构建：源码在 ${module}/，构建目录在源码树之外（铁律：源码区
#      不做编译）。构建成功即证明头引用、警告策略与链接面成立。
#   2. 冒烟运行：cloudnative 跑 agentctl 并核对 A-IPC magic 与头文件契约
#      一致；services 跑 CTest（dmn_test）证明 daemon 框架的单元判据成立。
#
# 只编一个模块时不碰另一个：流水线按模块拆成独立作业，一个模块失败不应
# 掩盖另一个模块的结论，也不该让两者的编译时间串行叠加。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/c-verify.sh --module cloudnative|services
EOF
}

MODULE=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --module) MODULE="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage >&2; exit "${CI_EINVAL}" ;;
    esac
done

case "${MODULE}" in
    cloudnative|services) ;;
    '') err "--module 必填"; usage >&2; exit "${CI_EINVAL}" ;;
    *) err "--module 只接受 cloudnative|services"; exit "${CI_EINVAL}" ;;
esac

[ -d "${ROOT}/${MODULE}" ] || die "模块目录不存在: ${MODULE}"
need_cmd cmake

# 构建目录固定落在源码树之外。这不是风格问题：在源码区生成 CMakeCache、
# 目标文件与编译数据库，会让下一次 checkout 与成品镜像都带上本地环境的
# 残留，也让「源码区无构建产物」这条铁律失去可检查性。
BUILD="${AIRY_C_BUILD_ROOT:-${TMPDIR:-/tmp}/airy-c-verify}/${MODULE}"
case "${BUILD}" in
    "${ROOT}"/*) die "构建目录不得位于源码树内: ${BUILD}" ;;
esac

rm -rf "${BUILD}"
mkdir -p "${BUILD}"

group "${MODULE}: 出树构建"
if ! cmake -S "${ROOT}/${MODULE}" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=Release >/dev/null; then
    endgroup
    die "${MODULE} 配置失败（构建目录 ${BUILD}）"
fi
# 不吞编译输出：警告与首个错误是唯一能定位 include 根、标准库与链接面问题
# 的证据，静默构建会让失败只剩一个退出码。
cmake --build "${BUILD}" --parallel 2>&1 | tail -n 40
rc=${PIPESTATUS[0]}
endgroup
[ "${rc}" -eq 0 ] || die "${MODULE} 构建失败（构建目录 ${BUILD}）"
say "${MODULE} 构建通过"

case "${MODULE}" in
    cloudnative)
        group "cloudnative: agentctl 契约冒烟"
        out="$("${BUILD}/agentctl/agentctl" 2>&1)" || {
            endgroup
            die "agentctl 无法运行: ${out}"
        }
        printf '%s\n' "${out}"
        case "${out}" in
            *"A-IPC magic=0x41524531"*) ;;
            *)
                endgroup
                die "agentctl 未报告与 [SC] ipc.h 一致的 magic"
                ;;
        esac
        endgroup
        say "agentctl 运行通过：A-IPC magic 与 [SC] 契约一致"
        ;;
    services)
        group "services: CTest"
        ctest --test-dir "${BUILD}" --output-on-failure || {
            endgroup
            die "services 单元用例未通过"
        }
        endgroup
        say "services 用例通过"
        ;;
esac
