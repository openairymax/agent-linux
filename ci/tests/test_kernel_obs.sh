#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_kernel_obs.sh — kernel-obs 门禁回归夹具
#
# 门禁判六组契约：注册时序、属性只读面、能力/预算面、Kbuild 清单、
# 配置开关、文件头。夹具先造一棵满足全部契约的最小源树，再逐组破坏一处，
# 断言只看退出码与稳定判据字符串。
#
# 用法: bash ci/tests/test_kernel_obs.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/kernel-obs.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

run() { GITHUB_ACTIONS='' bash "${GATE}" "$@" 2>&1; }

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

# src_file SRC NAME [BODY] — 写一个带文件头的观测面源文件
src_file()
{
    local obs="$1/kernel/obs" name="$2" body="${3:-}"

    {
        printf '// SPDX-License-Identifier: GPL-2.0-only\n'
        printf '// Copyright (C) 2025-2026 SPHARX Ltd.\n'
        printf '%s\n' "${body}"
    } >"${obs}/${name}"
}

mkvalid()
{
    local src="$1" cfg

    mkdir -p "${src}/kernel/obs" "${src}/configs" "${src}/security/airy"
    for cfg in configs/defconfig configs/defconfig-agent \
        configs/defconfig-embedded security/airy/.kunitconfig; do
        printf 'CONFIG_AIRY_OBS=y\n' >"${src}/${cfg}"
    done
    printf 'obj-$(CONFIG_AIRY_OBS) += obs/\n' >"${src}/kernel/Makefile"
    src_file "${src}" airy_obs.h \
        '#define AIRY_OBS_ATTR(_name) __ATTR(_name, 0444, _name##_show, NULL)'
    src_file "${src}" airy_obs_core.c \
        'core_initcall(airy_obs_start);
static void airy_obs_start(void) { airy_probe_exit(); }
static int kset_create_and_add(const char *n) { return 0; }
static void kset_setup(void) { kset_create_and_add("airy"); }'
    src_file "${src}" airy_obs_proc.c 'single_open(NULL, NULL, NULL);'
    src_file "${src}" airy_obs_superv.c \
        'airy_obs_dir(dir, "budgets");
AIRY_OBS_ATTR(budget_alerts);
static ssize_t budget_alerts_show(void) { return 0; }'
    src_file "${src}" airy_obs_ipc.c \
        'AIRY_OBS_ATTR(capabilities);
static ssize_t capabilities_show(void) { return 0; }'
    src_file "${src}" airy_token.c 'airy_obs_budgets(dir, agent);'
    {
        printf '// SPDX-License-Identifier: GPL-2.0-only\n'
        printf 'airy_obs-y := airy_obs_core.o airy_obs_proc.o'
        printf ' airy_obs_superv.o airy_obs_ipc.o airy_token.o\n'
    } >"${src}/kernel/obs/Kbuild"
}

# mutate NAME [FILE] [SED-EXPR] — 复制合规树并改一处，打印新树路径
mutate()
{
    local name="$1" rel="${2:-}" expr="${3:-}" src="${tmp}/$1" obs

    mkvalid "${src}"
    obs="${src}/kernel/obs"
    case "${name}" in
    drop-config)
        sed -i '/CONFIG_AIRY_OBS/d' \
            "${src}/security/airy/.kunitconfig"
        ;;
    extra-obj)
        printf 'stale_mod.o\n' >>"${obs}/Kbuild"
        ;;
    missing-dir)
        rm -rf "${src}/kernel/obs"
        ;;
    *)
        sed -i "${expr}" "${obs}/${rel}"
        ;;
    esac
    printf '%s\n' "${src}"
}

out="$(run --src "${tmp}/none")"; rc=$?
check "缺目录退 5" 5 "${rc}" "${out}" "--src 目录不存在"

out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

mkvalid "${tmp}/valid"
out="$(run --src "${tmp}/valid")"; rc=$?
check "合规树通过" 0 "${rc}" "${out}" "观测面契约门禁通过"

# 负向：注册时序
src="$(mutate late-init airy_obs_core.c 's/^core_initcall(/fs_initcall(/')"
out="$(run --src "${src}")"; rc=$?
check "注册退到 fs_initcall" 1 "${rc}" "${out}" "未在 core_initcall 发布"

src="$(mutate no-rollback airy_obs_core.c 's/airy_probe_exit();//')"
out="$(run --src "${src}")"; rc=$?
check "失败未撤回就绪标记" 1 "${rc}" "${out}" "未撤回 agentrt 就绪标记"

# 负向：属性只读面
src="$(mutate writable-attr airy_obs.h 's/0444/0644/')"
out="$(run --src "${src}")"; rc=$?
check "属性改为可写" 1 "${rc}" "${out}" "未固定为 0444 只读"

src="$(mutate raw-attr airy_obs_superv.c \
    's/^AIRY_OBS_ATTR(budget_alerts);/__ATTR(budget_alerts, 0444, NULL, NULL);/')"
out="$(run --src "${src}")"; rc=$?
check "绕过 AIRY_OBS_ATTR" 1 "${rc}" "${out}" "绕过 AIRY_OBS_ATTR"

src="$(mutate no-emit airy_obs_ipc.c \
    's/^static ssize_t capabilities_show.*/&\nstatic int x(void) { return snprintf(0, 0, ""); }/')"
out="$(run --src "${src}")"; rc=$?
check "输出未走 sysfs_emit" 1 "${rc}" "${out}" "输出未走 sysfs_emit"

# 负向：能力与预算面
src="$(mutate no-budget-dir airy_obs_superv.c 's/airy_obs_dir(dir, "budgets");//')"
out="$(run --src "${src}")"; rc=$?
check "superv 缺 budgets 目录" 1 "${rc}" "${out}" "未发布 budgets 目录"

# 负向：Kbuild 清单与配置开关
src="$(mutate extra-obj)"
out="$(run --src "${src}")"; rc=$?
check "Kbuild 清单多余对象" 1 "${rc}" "${out}" "Kbuild 清单与目录内容不一致"

src="$(mutate drop-config)"
out="$(run --src "${src}")"; rc=$?
check "配置开关缺席" 1 "${rc}" "${out}" "未开启 CONFIG_AIRY_OBS"

# 负向：文件头与目录缺席
src="$(mutate no-spdx airy_token.c '1s/.*/\/\/ no license header/')"
out="$(run --src "${src}")"; rc=$?
check "源文件首行缺 SPDX" 1 "${rc}" "${out}" "首行缺少 SPDX"

src="$(mutate missing-dir)"
out="$(run --src "${src}")"; rc=$?
check "观测面目录缺席退 5" 5 "${rc}" "${out}" "缺少观测面目录"

if [ "${fails}" -ne 0 ]; then
    printf 'test_kernel_obs: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_kernel_obs: 全部通过\n'
