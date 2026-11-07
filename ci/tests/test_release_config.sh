#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_config.sh — ci/gates/release-config.sh 的分支回归
#
# 快照门禁的判定对象是内核子仓里的四份 airy_defconfig 与一份全架构片段
# configs/defconfig-agent：真实内核树不在单测里构建，判定逻辑只能由合成夹具
# 驱动。这里造一棵最小内核树，再逐条破坏单一前提（缺一腿、缺全架构片段、
# 缺选型配置、启用 sched_ext、缺参数），断言只看退出码、稳定判据名与归档内容。
#
# 确定性不靠注释承诺：同一份夹具两次调用必须字节相同，否则摘要清单在
# 重发时对不上——这一条在测试里断言，而不是等发布页出事。
#
# 用法: bash ci/tests/test_release_config.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/release-config.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
ARCHS=(x86 arm64 riscv loongarch)
EPOCH=1700000000

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${GATE}" "${FX}/ci/gates/release-config.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

# 造一棵只剩配置片段的内核树；调用方按需改写某个架构的文件。
seed()
{
    local arch

    for arch in "${ARCHS[@]}"; do
        mkdir -p "${FX}/kernel/arch/${arch}/configs"
        cat >"${FX}/kernel/arch/${arch}/configs/airy_defconfig" <<'EOF'
CONFIG_SECURITY=y
CONFIG_SECURITY_AIRY=y
CONFIG_IO_URING=y
# CONFIG_BPF_LSM is not set
CONFIG_BPF_UNPRIV_DEFAULT_OFF=y
EOF
    done
    mkdir -p "${FX}/kernel/configs"
    cat >"${FX}/kernel/configs/defconfig-agent" <<'EOF'
# CONFIG_RT_GROUP_SCHED is not set
# CONFIG_IMA_LOAD_X509 is not set
EOF
}

run()
{
    ( cd "${tmp}" && SOURCE_DATE_EPOCH="${EPOCH}" \
        bash "${FX}/ci/gates/release-config.sh" "$@" 2>&1 )
}

# expect —— 单次调用的退出码与输出关键判据。
expect()
{
    local name="$1" want="$2" needle="$3"
    shift 3
    local out rc
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" != "${want}" ]; then
        bad "${name}（rc=${rc}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    if [ -n "${needle}" ] && ! printf '%s' "${out}" | grep -qF "${needle}"; then
        bad "${name}（输出缺少「${needle}」）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    ok "${name}"
}

seed
expect "四架构成快照" 0 "agent-linux-0.0.4-airy-defconfig.tar.xz" \
    snapshot --ver 0.0.4 --out "${tmp}/a"
expect "再次打包字节相同" 0 "" snapshot --ver 0.0.4 --out "${tmp}/b"

if cmp -s "${tmp}/a/agent-linux-0.0.4-airy-defconfig.tar.xz" \
    "${tmp}/b/agent-linux-0.0.4-airy-defconfig.tar.xz"; then
    ok "两次调用产物逐字节一致"
else
    bad "两次调用产物不一致（确定性被破坏）"
fi

want="$(
    printf '%s\n' aarch64-airy_defconfig loongarch64-airy_defconfig \
        riscv64-airy_defconfig x86_64-airy_defconfig common-agent.fragment README |
        sort
)"
got="$(tar -tf "${tmp}/a/agent-linux-0.0.4-airy-defconfig.tar.xz" | sort)"
[ "${got}" = "${want}" ] && ok "归档含四份架构配置、全架构片段与 README" ||
    bad "归档内容不符：$(printf '%s ' ${got})"

# 全架构片段是 agent 层取舍的唯一载体（RT 带宽与 IMA 的「未启用」只写在
# 这里）。丢了它，取件方照 README 合并出的 .config 与发行包就不是同一份。
rm -f "${FX}/kernel/configs/defconfig-agent"
expect "缺全架构片段即判负" 1 "缺少" snapshot --ver 0.0.4 --out "${tmp}/h"

seed
printf 'CONFIG_DEBUG_INFO_NONE=y\n' >"${FX}/kernel/configs/defconfig-agent"
expect "全架构片段丢 RT 判据即判负" 1 '# CONFIG_RT_GROUP_SCHED is not set' \
    snapshot --ver 0.0.4 --out "${tmp}/i"

seed
rm -f "${FX}/kernel/arch/riscv/configs/airy_defconfig"
expect "缺一腿即判负" 1 "缺少" snapshot --ver 0.0.4 --out "${tmp}/c"

seed
sed -i '/CONFIG_IO_URING=y/d' "${FX}/kernel/arch/arm64/configs/airy_defconfig"
expect "选型配置缺失即判负" 1 "CONFIG_IO_URING=y" snapshot --ver 0.0.4 --out "${tmp}/d"

# 非特权 BPF 的默认值只能由编译期决定：内核在 0.06 s 就会按它决定是否打印
# Spectre V2 告警，运行期再写 sysctl 已经来不及，所以片段丢这一行必须在
# 打包前判负，而不是留到装机后的启动日志里。
seed
sed -i '/CONFIG_BPF_UNPRIV_DEFAULT_OFF=y/d' \
    "${FX}/kernel/arch/riscv/configs/airy_defconfig"
expect "非特权 BPF 取默认关缺失即判负" 1 "CONFIG_BPF_UNPRIV_DEFAULT_OFF=y" \
    snapshot --ver 0.0.4 --out "${tmp}/g"

seed
printf 'CONFIG_SCHED_EXT=y\n' >>"${FX}/kernel/arch/x86/configs/airy_defconfig"
expect "启用 sched_ext 即判负" 1 "sched_ext" snapshot --ver 0.0.4 --out "${tmp}/e"

seed
expect "缺 --ver 退出 5" 5 "缺少 --ver" snapshot --out "${tmp}/f"
expect "未知子命令退出 5" 5 "未知子命令" freeze --ver 0.0.4

if [ "${fails}" -gt 0 ]; then
    printf '\n%d 项断言失败\n' "${fails}"
    exit 1
fi
printf '\nrelease-config: 全部断言通过\n'
