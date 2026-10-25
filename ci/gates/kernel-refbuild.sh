#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-refbuild.sh — x86_64 airy_defconfig 参考构建
#
# 用法:
#   ci/gates/kernel-refbuild.sh [--arch x86_64] [--src DIR] [--log FILE]
#
# verify 腿里除矩阵之外的所有检查（sparse、Coccinelle、kselftest）都需要
# 一份「确定配置」的产物：不是任意矩阵格，而是 airy_defconfig 在 x86_64
# 上的结果。此前这段配置+编译+断言散在 ci-kernel.yml 的一个长 run 块里，
# 与 sparse 步骤靠「上一步产物」隐式耦合——重排步骤顺序就会静默退化成
# 对空对象做检查。独立成 gate 后，顺序依赖变成显式的前置命令。
#
# 断言分三层:
#   1. airy_defconfig 合并后 10 个关键 CONFIG 必须精确匹配（不是包含）；
#   2. CONFIG_LSM 里必须出现 airy；
#   3. vmlinux 必须带 .BTF 段——Linux 6.6 的 BTF 由 gen_btf() 内嵌进
#      该段，不产出独立 vmlinux.btf 文件（OS-OBS-012）。
#
# 退出码: 0 产物就绪；1 构建或断言失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=kernel-refbuild
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
ARCH=x86_64
LOG="build.log"

readonly AIRY_RE='security/airy/|kernel/(superv|syscalls|corekern|log|ipc)/|/airymax/'

# airy_defconfig 合并后必须出现的精确行。写成完整 KV 而不是 K 前缀，
# 是为了同时锁住开关的值（例如 y 被改成 m 也算红）。
readonly REQUIRED_CONF=(
    'CONFIG_SECURITY=y'
    'CONFIG_SECURITY_AIRY=y'
    'CONFIG_AIRY_SYSCALL=y'
    'CONFIG_AIRY_COREKERN=y'
    'CONFIG_AIRY_LOG=y'
    'CONFIG_AIRY_IPC=y'
    'CONFIG_AIRY_AGENT_TRACING=y'
    'CONFIG_AIRY_CAP_TABLE_SIZE=1024'
    'CONFIG_IO_URING=y'
    'CONFIG_USER_NS=y'
)

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-refbuild.sh [--arch x86_64] [--src DIR] [--log FILE]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --log) LOG="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

# verify 腿的全部下游检查都在 x86_64 上做，其它架构请用 kernel-build.sh。
[ "${ARCH}" = x86_64 ] || {
    note "参考构建只支持 x86_64"
    exit "${EINVAL}"
}

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"

cd "${SRC}" || die "无法进入 ${SRC}"

: >"${LOG}"
group "配置 airy_defconfig / x86_64"
make ARCH=x86_64 defconfig >>"${LOG}" 2>&1 || die "defconfig 阶段失败"
./scripts/kconfig/merge_config.sh -m .config arch/x86/configs/airy_defconfig \
    >>"${LOG}" 2>&1 || die "merge_config 阶段失败"
make ARCH=x86_64 olddefconfig >>"${LOG}" 2>&1 || die "olddefconfig 阶段失败"
endgroup

group "断言关键 CONFIG"
for want in "${REQUIRED_CONF[@]}"; do
    grep -qx "${want}" .config || die "airy_defconfig 输出缺少 ${want}"
done
lsm="$(sed -n 's/^CONFIG_LSM="\(.*\)"$/\1/p' .config)"
case ",${lsm}," in
*,airy,*) say "CONFIG_LSM 含 airy: ${lsm}" ;;
*) die "CONFIG_LSM 不含 airy: ${lsm}" ;;
esac
endgroup

group "编译 vmlinux + bzImage"
make ARCH=x86_64 -j"$(nproc)" >>"${LOG}" 2>&1 || die "内核编译失败"
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
fi
endgroup

[ -f arch/x86/boot/bzImage ] || die "缺少 arch/x86/boot/bzImage"
[ -f vmlinux ] || die "缺少 vmlinux"

readelf -S vmlinux | grep -q '\.BTF' ||
    die "vmlinux 缺少 .BTF 段（CONFIG_DEBUG_INFO_BTF 未生效）"

say "产物就绪: bzImage + vmlinux(.BTF)"
say "bzImage = ${SRC}/arch/x86/boot/bzImage"
