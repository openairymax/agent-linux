#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/c-verify.sh — 用户态 C 组件的构建与冒烟门禁
#
# 用法:
#   ci/gates/c-verify.sh --module cloudnative|services|system|tests-linux
#
# 这些模块长期没有进入任何流水线：services 只在本地被手工 `cmake --build`
# 过，cloudnative 则因为 include 根把内核内部头排在 libc 之前而一直编不过
# （`blkcnt_t` 冲突），却在 OS-CHK 的静态检查里显示全绿。构建面的失真比
# 静态检查漏报更危险——它让「能跑」失去判据，因此这里用真实编译补上。
# system 与 tests-linux 同理：CMake 入口与 CTest 用例早已写好，但仓库内
# 没有任何作业执行过它们，等于断言从未生效。
#
# 判据分两步，缺一不可：
#   1. 出树构建：源码在 ${module}/，构建目录在源码树之外（铁律：源码区
#      不做编译）。构建成功即证明头引用、警告策略与链接面成立。
#   2. 冒烟运行：cloudnative 跑 agentctl 并核对 A-IPC magic 与头文件契约
#      一致；其余模块跑 CTest，把已登记的单元判据真正执行一遍。
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
用法: ci/gates/c-verify.sh --module cloudnative|services|system|tests-linux
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
    cloudnative|services|system|tests-linux) ;;
    '') err "--module 必填"; usage >&2; exit "${CI_EINVAL}" ;;
    *) err "--module 只接受 cloudnative|services|system|tests-linux"
       exit "${CI_EINVAL}" ;;
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

# tests-linux 是用户态测试，按 <linux/airymax/...> 引用 [SC] 契约头；契约头
# 经 uapi_compat.h 拉入 <linux/types.h> 与 <linux/stddef.h>。把源码树的
# kernel/include/uapi 直接当 include 根只能拿到原样内核头：types.h 因缺少
# __EXPORTED_HEADERS__ 报「Attempt to use kernel headers from user space」，
# stddef.h 再去找只存在于内核内部的 linux/compiler_types.h。内核为此提供
# 的出口就是 headers_install，它把 uapi 头导出成用户态可用形态。这里先导出
# 再构建；O= 让内核侧生成物（scripts/、generated/version.h）也落在源码树外。
HDR_ARGS=()
if [ "${MODULE}" = tests-linux ]; then
    need_cmd make
    group "tests-linux: 导出 UAPI 头"
    if ! make -C "${ROOT}/kernel" O="${BUILD}/kobj" \
            headers_install INSTALL_HDR_PATH="${BUILD}/hdr" >/dev/null; then
        endgroup
        die "UAPI 头导出失败（内核 O=${BUILD}/kobj）"
    fi
    endgroup
    HDR_ARGS=(-DAIRY_HDR_ROOT="${BUILD}/hdr/include")
fi

group "${MODULE}: 出树构建"
if ! cmake -S "${ROOT}/${MODULE}" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=Release ${HDR_ARGS[@]+"${HDR_ARGS[@]}"} >/dev/null; then
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
    *)
        group "${MODULE}: CTest"
        ctest --test-dir "${BUILD}" --output-on-failure || {
            endgroup
            die "${MODULE} 单元用例未通过"
        }
        endgroup
        say "${MODULE} 用例通过"
        ;;
esac
