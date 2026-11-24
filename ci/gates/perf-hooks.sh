#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/perf-hooks.sh — perf 采样入口（P-10）契约与出树构建门禁
#
# 用法:
#   ci/gates/perf-hooks.sh
#
# perf 集成最容易退化成两种空壳：一是自己解析 perf.data，二是把统计口径
# 固定在人类可读输出上，让自动归集端无从消费。前者会随内核格式漂移而静默
# 给出错的数，后者让基准数据只能靠人看。故门禁把「只编排上游 perf、只产出
# 机器可读」固化成静态判据，再补一次真实编译与三条失败路径。
#
# 判据：
#   * 目录只放源码与 CMakeLists，不落产物。
#   * 统计走 `perf stat -x,`（OS-OBS-032），事件集为六大硬件计数器。
#   * 采样为 99Hz、DWARF 调用栈（§3.1），报告取前 20 项（OS-OBS-033）。
#   * 只经 execvp 执行上游 perf；不含 system/popen，不解析 perf.data。
#   * 不写 sysfs/procfs：irq_balancing 只以只读方式记录（OS-STD-022）。
#   * 产物路径固定在 /var/log/airy，report 输出 JSON 字段 overhead/symbol/dso。
#   * 出树构建通过 -Wall -Wextra -Werror；--help 退 0，坏参数退 2，缺输入退 4。
#
# 退出码: 0 通过；1 契约或构建不成立；5 参数或目录不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/apt.sh
. "${ROOT}/ci/lib/apt.sh"

SRC_DIR="${ROOT}/system/obs/perf"
SRC="${SRC_DIR}/airy_perf.c"
CMAKE="${SRC_DIR}/CMakeLists.txt"

need_dir --src "${SRC_DIR}"
need_file "${SRC}" "采样入口源码"
need_file "${CMAKE}" "构建脚本"

chk_files()
{
	local got

	group "目录与头注释"
	got="$(find "${SRC_DIR}" -mindepth 1 -maxdepth 1 -printf '%f\n' |
		LC_ALL=C sort | tr '\n' ' ')"
	if [ "${got}" != 'CMakeLists.txt airy_perf.c ' ]; then
		bad "目录内容不符: ${got}"
	fi
	[ "$(sed -n '1p' "${SRC}")" = '// SPDX-License-Identifier: GPL-2.0-only' ] ||
		bad "airy_perf.c: 首行缺少 SPDX"
	sed -n '1,6p' "${SRC}" | grep -q 'Copyright' ||
		bad "airy_perf.c: 缺少版权行"
	have 'SPDX-License-Identifier' "${CMAKE}" "CMakeLists 缺少 SPDX"
	endgroup
}

chk_stat()
{
	group "统计口径"
	have '"-x,"' "${SRC}" "统计未使用 CSV 输出（OS-OBS-032）"
	have 'cycles,instructions,cache-misses' "${SRC}" "事件集缺少硬件计数器"
	have 'branch-misses,branch-instructions' "${SRC}" "事件集缺少分支计数器"
	have '#define PF_FREQ[[:space:]]+99' "${SRC}" "采样频率不是 99Hz"
	have '"--call-graph"' "${SRC}" "record 未指定调用栈"
	have '"dwarf"' "${SRC}" "调用栈不是 DWARF"
	have '#define PF_TOP[[:space:]]+20' "${SRC}" "报告未取前 20 项"
	endgroup
}

chk_scope()
{
	group "边界"
	have 'execvp\(' "${SRC}" "未通过 execvp 调用上游 perf"
	deny 'system\(' "${SRC}" "经 shell 启动外部命令"
	deny 'popen\(' "${SRC}" "经管道 shell 取输出"
	deny 'tools/perf' "${SRC}" "引用了 perf 源码路径"
	have '/var/log/airy' "${SRC}" "产物未落 /var/log/airy"
	have '\\"overhead\\"' "${SRC}" "报告缺少 overhead 字段"
	have '\\"symbol\\"' "${SRC}" "报告缺少 symbol 字段"
	have '\\"dso\\"' "${SRC}" "报告缺少 dso 字段"
	endgroup
}

chk_sysfs()
{
	group "只读约束"
	have '"/proc/sys/kernel/irq_balancing"' "${SRC}" "未记录 irq_balancing"
	have 'fopen\(PF_IRQ, "r"\)' "${SRC}" "irq_balancing 不是只读打开"
	deny 'fopen\(PF_IRQ, "w"' "${SRC}" "写入了 irq_balancing"
	deny '"/sys/' "${SRC}" "工具直接访问 sysfs"
	deny 'sched_setscheduler' "${SRC}" "工具干预调度策略"
	endgroup
}

deps()
{
	if command -v cmake >/dev/null 2>&1 && command -v cc >/dev/null 2>&1; then
		return 0
	fi
	note "安装 perf 入口构建依赖"
	apt_sync && apt_install_min cmake gcc libc6-dev
}

build_tree()
{
	local dir="$1" log="$1/build.log"

	if ! deps; then
		bad "构建依赖不可用"
		return 1
	fi
	if ! cmake -S "${SRC_DIR}" -B "${dir}/b" -DCMAKE_BUILD_TYPE=Release \
		>"${log}" 2>&1; then
		bad "cmake 配置失败"
		tail -n 20 "${log}" >&2
		return 1
	fi
	if ! cmake --build "${dir}/b" -j "$(nproc)" >>"${log}" 2>&1; then
		bad "编译失败"
		tail -n 40 "${log}" >&2
		return 1
	fi
}

# chk_cli BIN —— 帮助、坏参数、缺输入三条入口判据，全部不需要 perf。
chk_cli()
{
	local bin="$1"
	local rc=0

	"${bin}" --help >/dev/null 2>&1 || rc=$?
	[ "${rc}" -eq 0 ] || bad "--help 未退 0"
	rc=0
	"${bin}" stat --duration 0 >/dev/null 2>&1 || rc=$?
	[ "${rc}" -eq 2 ] || bad "坏参数未退 2"
	rc=0
	"${bin}" no-such-cmd >/dev/null 2>&1 || rc=$?
	[ "${rc}" -eq 2 ] || bad "未知子命令未退 2"
	rc=0
	"${bin}" report --input "${bin}.absent" >/dev/null 2>&1 || rc=$?
	[ "${rc}" -eq 4 ] || bad "缺输入未退 4"
}

# chk_sample BIN DIR —— 有 perf 时跑一次 1 秒真实采样并校验 CSV。
chk_sample()
{
	local bin="$1" dir="$2" csv="$2/stat.csv"

	if ! command -v perf >/dev/null 2>&1; then
		note "宿主无 perf，跳过实跑（285H 侧验收）"
		return 0
	fi
	if ! "${bin}" stat --duration 1 --out "${csv}" >/dev/null 2>&1; then
		bad "1 秒采样失败"
		return 1
	fi
	grep -q 'cycles' "${csv}" || bad "CSV 中无 cycles 计数"
}

chk_build()
{
	local tmp

	group "出树构建"
	tmp="$(mktemp -d)"
	if [ -z "${tmp}" ]; then
		bad "临时目录创建失败"
		endgroup
		return
	fi
	if build_tree "${tmp}"; then
		if [ -x "${tmp}/b/airy-perf" ]; then
			chk_cli "${tmp}/b/airy-perf"
			chk_sample "${tmp}/b/airy-perf" "${tmp}"
		else
			bad "缺少 airy-perf 产物"
		fi
	fi
	rm -rf "${tmp}"
	endgroup
}

chk_files
chk_stat
chk_scope
chk_sysfs
chk_build

if [ "${FAILS}" -ne 0 ]; then
	err "perf 采样入口门禁失败 ${FAILS} 项"
	exit 1
fi

say "perf 采样入口门禁通过"
exit 0
