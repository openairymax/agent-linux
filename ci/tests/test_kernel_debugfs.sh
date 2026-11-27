#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_kernel_debugfs.sh — kernel-debugfs 门禁回归夹具
#
# 门禁只读内核源，因此可以在一棵最小假树上逐条破坏它的判据。这里钉住的
# 是「注册时序 / 只读接口 / 节点面 / 机密边界 / 构建配置 / 文件头」六组
# 断言的落点：每条负向只改一处，断言只看退出码与稳定判据字符串。
#
# 用法: bash ci/tests/test_kernel_debugfs.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/kernel-debugfs.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 门禁在 Actions 里以 ::error:: 打印判据，本地是 error: 前缀；两种都接受。
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

# mkvalid DIR — 造一棵满足全部判据的最小内核源树
mkvalid()
{
    local src="$1" arch

    mkdir -p "${src}/kernel/obs"
    for arch in x86 arm64 riscv loongarch; do
        mkdir -p "${src}/arch/${arch}/configs"
        printf 'CONFIG_DEBUG_FS=y\nCONFIG_TRACING=y\nCONFIG_EVENT_TRACING=y\n' \
            >"${src}/arch/${arch}/configs/airy_defconfig"
    done
    cat >"${src}/kernel/obs/airy_obs_debugfs.c" <<'EOF'
// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2025-2026 SPHARX Ltd.
#include <linux/debugfs.h>

static void mk_node(struct dentry *dir, const char *name) { }
static void dbg_group(const char *name) { }

static int airy_dbg_start(void)
{
        struct dentry *dir = NULL;

        if (!airy_obs_ready()) {
                pr_warn("airy: debugfs 未就绪\n");
                return 0;
        }
        dbg_group("sec");
        dbg_group("superv");
        dbg_group("log");
        mk_node(dir, "cap_table");
        mk_node(dir, "last_decision");
        mk_node(dir, "ring_buffer_dump");
        debugfs_create_file("cap_table", 0444, NULL, NULL, NULL);
        return 0;
}
fs_initcall(airy_dbg_start);
EOF
    printf '// SPDX-License-Identifier: GPL-2.0-only\n' \
        >"${src}/kernel/obs/airy_obs_sec.c"
    printf 'int airy_obs_cap_fmt(void) { return 0; }\n' \
        >>"${src}/kernel/obs/airy_obs_sec.c"
    printf 'airy_obs-$(CONFIG_DEBUG_FS) += airy_obs_debugfs.o\n' \
        >"${src}/kernel/obs/Kbuild"
}

# mutate NAME SED-EXPR — 复制一份合规树并改一处，打印新树路径
mutate()
{
    local name="$1" expr="$2" src="${tmp}/$1"

    mkvalid "${src}"
    case "${expr}" in
    drop-spdx)
        sed -i '1s/.*/\/\/ no license header/' \
            "${src}/kernel/obs/airy_obs_debugfs.c"
        ;;
    drop-config)
        sed -i '/CONFIG_EVENT_TRACING/d' \
            "${src}/arch/riscv/configs/airy_defconfig"
        ;;
    *)
        sed -i "${expr}" "${src}/kernel/obs/airy_obs_debugfs.c"
        ;;
    esac
    printf '%s\n' "${src}"
}

# 负向：参数与目录
out="$(run --src "${tmp}/none")"; rc=$?
check "缺目录退 5" 5 "${rc}" "${out}" "--src 目录不存在"

out="$(run --bogus)"; rc=$?
check "未知参数退 5" 5 "${rc}" "${out}" "未知参数"

out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

# 正向：合规树
mkvalid "${tmp}/valid"
out="$(run --src "${tmp}/valid")"; rc=$?
check "合规树通过" 0 "${rc}" "${out}" "调试面契约门禁通过"

# 负向：注册时序与投影接口
src="$(mutate early-init 's/^fs_initcall(/core_initcall(/')"
out="$(run --src "${src}")"; rc=$?
check "注册提前到 core_initcall" 1 "${rc}" "${out}" "未在 fs_initcall 注册"

src="$(mutate no-ready 's/if (!airy_obs_ready())/if (0)/')"
out="$(run --src "${src}")"; rc=$?
check "缺就绪标记依赖" 1 "${rc}" "${out}" "未依赖 /sys/airy 的就绪标记"

src="$(mutate writable-projection \
    's/^fs_initcall.*/&\ndebugfs_create_u32("x", 0444, NULL, NULL);/')"
out="$(run --src "${src}")"; rc=$?
check "使用可写投影接口" 1 "${rc}" "${out}" "可写投影接口"

src="$(mutate non-ro 's/^fs_initcall.*/&\ndebugfs_create_file("y", 0644, NULL, NULL, NULL);/')"
out="$(run --src "${src}")"; rc=$?
check "非 0444 的调试文件" 1 "${rc}" "${out}" "非 0444"

# 负向：节点面与机密边界
src="$(mutate missing-node 's/mk_node(dir, "cap_table");//')"
out="$(run --src "${src}")"; rc=$?
check "缺调试节点" 1 "${rc}" "${out}" "缺少调试节点 cap_table"

src="$(mutate randtag 's/^fs_initcall.*/&\n\/\* randtag \*\//')"
out="$(run --src "${src}")"; rc=$?
check "观测面引用防伪随机标签" 1 "${rc}" "${out}" "防伪随机标签"

# 负向：构建配置与文件头
src="$(mutate no-event-tracing drop-config)"
out="$(run --src "${src}")"; rc=$?
check "defconfig 缺 EVENT_TRACING" 1 "${rc}" "${out}" \
    "riscv/airy_defconfig 未开启 CONFIG_EVENT_TRACING"

src="$(mutate no-spdx drop-spdx)"
out="$(run --src "${src}")"; rc=$?
check "源文件首行缺 SPDX" 1 "${rc}" "${out}" "首行缺少 SPDX"

# 负向：源文件缺席
src="${tmp}/no-src"
mkvalid "${src}"
rm -f "${src}/kernel/obs/airy_obs_debugfs.c"
out="$(run --src "${src}")"; rc=$?
check "缺调试面源文件退 5" 5 "${rc}" "${out}" "缺少调试面源文件"

if [ "${fails}" -ne 0 ]; then
    printf 'test_kernel_debugfs: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_kernel_debugfs: 全部通过\n'
