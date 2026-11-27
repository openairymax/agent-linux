#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/kernel-policy.sh — 决策表内核运行时契约门禁
#
# 用法:
#   ci/gates/kernel-policy.sh [--src DIR]
#
# .adl 是策略的唯一事实来源，工具链把它渲染成纯 C 决策表头，内核在
# airy_init 里注册这三张表。三处任一处脱节都不会在编译期报错：字段名
# 不在运行期登记表里时注册返回 -EINVAL，但启动路径只记一条 pr_err，
# 系统照常起来，策略面却静默为空。因此把契约固化成静态门禁。
#
# 判据：
#   * .adl 声明三张表，钩子与输入字段等于运行期字段登记表的名字。
#   * 生成器把同一契约登记为两份产物（示例与内核树），两份逐字节一致。
#   * 内核侧消费者只注册三张表，原型、Kbuild 对象与 airy_init 调用齐备，
#     且调用位于 W^X 表注册之后（先确保强制面成立）。
#   * KUnit 有用例，TAP 基线登记同一用例名。
#   * 三张表里只有 uring_guard 保留 DENY；task/file 两表不得阻断已通过
#     能力检查的操作。
#   * 两个内核源文件带 SPDX 与版权行，流水线调用本门禁。
#
# 退出码: 0 通过；1 契约不成立；5 参数或目录不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/opts.sh
. "${ROOT}/ci/lib/opts.sh"

SRC="${ROOT}/kernel"
opts_src kernel-policy.sh "$@"

ADL="${ROOT}/tools/dsl/examples/security_policy.adl"
COMPILER="${ROOT}/tools/dsl/dsl_compile.py"
EXAMPLE_HDR="${ROOT}/tools/dsl/examples/security_policy_tables.h"
KERNEL_HDR="${SRC}/security/airy/security_policy_tables.h"
CONSUMER="${SRC}/security/airy/airy_policy_tables.c"
RUNTIME="${SRC}/security/airy/airy_policy.c"
LSM="${SRC}/security/airy/airy_lsm.c"
API_HDR="${SRC}/include/airymax/airy_policy.h"
TEST="${SRC}/security/airy/airy_policy_test.c"
TAP="${ROOT}/tools/tap/kunit-tap-baseline.tap"
WORKFLOW="${ROOT}/.github/workflows/kernel-verify.yml"

for f in "${ADL}" "${COMPILER}" "${KERNEL_HDR}" "${CONSUMER}" \
         "${RUNTIME}" "${LSM}" "${API_HDR}" "${TEST}" "${TAP}" \
         "${WORKFLOW}"; do
    need_file "${f}" 契约文件
done

# 契约源 -------------------------------------------------------------------
chk_source()
{
    local table hook field

    group "契约源"
    have '^version[[:space:]]+"1\.0\.1";$' "${ADL}" "契约版本不是 1.0.1"
    while IFS='|' read -r table hook; do
        have "^table ${table} \{" "${ADL}" "缺少表 ${table}"
        have "hook = ${hook};" "${ADL}" "${table} 未绑定钩子 ${hook}"
    done <<'EOF'
uring_guard|uring_cmd
task_guard|task_alloc
file_open_guard|file_open
EOF
    while IFS='|' read -r table field; do
        have "^[[:space:]]*input ${field} : " "${ADL}" \
            "${table} 缺少运行期字段 ${field}"
    done <<'EOF'
uring_guard|op
uring_guard|cmd_flags
task_guard|clone_flags
task_guard|task_type
file_open_guard|acc_mode
file_open_guard|open_flags
EOF
    deny 'cap_flags|path_class' "${ADL}" \
        "契约源仍引用运行期不存在的字段（注册会返回 -EINVAL）"
    endgroup
}

# 运行期字段登记 -----------------------------------------------------------
chk_runtime()
{
    local field

    group "运行期字段"
    while IFS= read -r field; do
        have "\"${field}\"," "${RUNTIME}" \
            "运行期字段登记表缺少 ${field}"
    done <<'EOF'
op
cmd_flags
clone_flags
task_type
acc_mode
open_flags
EOF
    endgroup
}

# 生成物 -------------------------------------------------------------------
chk_generated()
{
    group "生成物"
    have 'security_policy_tables\.h"' "${COMPILER}" \
        "生成器未登记内核侧产物"
    have 'Path\("kernel"\) / "security" / "airy"' "${COMPILER}" \
        "生成器未把内核树目录登记为输出位置"
    if cmp -s "${EXAMPLE_HDR}" "${KERNEL_HDR}"; then
        say "两份生成物一致"
    else
        bad "示例与内核侧生成物不一致，请重跑 dsl_compile.py"
    fi
    for f in "${EXAMPLE_HDR}" "${KERNEL_HDR}"; do
        sed -n '1p' "${f}" | grep -q 'SPDX-License-Identifier:' ||
            bad "$(basename "${f}"): 首行缺少 SPDX"
        have 'DO NOT EDIT' "${f}" "$(basename "${f}"): 缺少禁止手改标记"
        have 'airy_policy_uring_guard_table' "${f}" \
            "$(basename "${f}"): 缺少 uring_guard 表"
        have 'airy_policy_task_guard_table' "${f}" \
            "$(basename "${f}"): 缺少 task_guard 表"
        have 'airy_policy_file_open_guard_table' "${f}" \
            "$(basename "${f}"): 缺少 file_open_guard 表"
    done
    # 只有 uring_guard 允许判负：另外两表是观测基线，不能拦住已通过能力
    # 检查的创建与打开。
    have '\.verdict = 1, \.fault = 0x100A' "${KERNEL_HDR}" \
        "uring_guard 未保留保留操作码的 DENY 行"
    deny '\.verdict = 1, \.fault = 0 \}' "${KERNEL_HDR}" \
        "观测表出现 DENY 行，会阻断已通过能力检查的操作"
    endgroup
}

# 内核消费者 ---------------------------------------------------------------
chk_consumer()
{
    group "内核消费者"
    have '#include "security_policy_tables\.h"' "${CONSUMER}" \
        "消费者未引入生成头"
    have 'airy_policy_uring_guard_table,' "${CONSUMER}" \
        "消费者未注册 uring_guard"
    have 'airy_policy_task_guard_table,' "${CONSUMER}" \
        "消费者未注册 task_guard"
    have 'airy_policy_file_open_guard_table,' "${CONSUMER}" \
        "消费者未注册 file_open_guard"
    have 'airy_policy_register\(airy_policy_tables\[i\]\)' "${CONSUMER}" \
        "消费者未走 airy_policy_register"
    have 'int airy_policy_tables_register\(void\);' "${API_HDR}" \
        "缺少 airy_policy_tables_register 原型"
    have 'airy_policy_tables\.o' "${SRC}/security/airy/Kbuild" \
        "Kbuild 未引入 airy_policy_tables.o"
    endgroup
}

# 启动顺序 -----------------------------------------------------------------
chk_boot()
{
    local reg tables

    group "启动顺序"
    reg=$(grep -n 'airy_policy_register(&airy_wx_table)' "${LSM}" |
          head -n1 | cut -d: -f1)
    tables=$(grep -n 'airy_policy_tables_register()' "${LSM}" |
             head -n1 | cut -d: -f1)
    if [ -z "${reg}" ] || [ -z "${tables}" ]; then
        bad "airy_init 缺少 W^X 表或参考策略的注册调用"
    elif [ "${tables}" -le "${reg}" ]; then
        bad "参考策略必须在 W^X 表之后注册，当前顺序相反"
    fi
    endgroup
}

# 测试与基线 ---------------------------------------------------------------
chk_tests()
{
    group "测试与基线"
    have 'KUNIT_CASE\(policy_tables\)' "${TEST}" \
        "KUnit 未登记 policy_tables 用例"
    have 'airy_policy_tables_register\(\)' "${TEST}" \
        "policy_tables 用例未注册参考策略"
    have '^[[:space:]]+ok [0-9]+ policy_tables$' "${TAP}" \
        "TAP 基线缺少 policy_tables"
    endgroup
}

# 文件头与流水线 -----------------------------------------------------------
chk_headers()
{
    local f

    group "文件头与流水线"
    for f in "${CONSUMER}" "${KERNEL_HDR}"; do
        sed -n '1,5p' "${f}" | grep -q 'Copyright' ||
            bad "$(basename "${f}"): 缺少版权行"
    done
    have 'ci/ci\.sh kernel-policy' "${WORKFLOW}" \
        "kernel-verify.yml 未调用 kernel-policy 门禁"
    endgroup
}

chk_source
chk_runtime
chk_generated
chk_consumer
chk_boot
chk_tests
chk_headers

if [ "${FAILS}" -ne 0 ]; then
    err "决策表契约门禁失败 ${FAILS} 项"
    exit 1
fi

say "决策表契约门禁通过"
exit 0
