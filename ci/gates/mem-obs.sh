#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/mem-obs.sh — 记忆计量（P-12）契约、口径与出树构建门禁
#
# 用法:
#   ci/gates/mem-obs.sh
#
# 记忆计量有三处容易滑走的错位：一是把处置（压缩、驱逐、拒绝写入）塞回
# 内核，让观测面变成策略面；二是四层各写一套比例与档位，同一个 Agent 的
# 数在 TUI、日志与告警里互相对不上；三是占用量用增量上报，重启后无法下调。
# 门禁把「内核只出事实、比例一律 ppm、占用绝对覆盖、四层同一套字段」固化
# 成静态判据，再做一次导出头构建与全部命令行路径。
#
# 判据：
#   * 目录只放源码与 CMakeLists，不落产物；两侧首行均为 SPDX。
#   * UAPI 契约：ABI 版本、60 秒窗口、四层编号、四条 ioctl、四处布局断言。
#   * 内核侧：Kbuild 与 core 初始化接线、KUnit suite、TAP 基线棘轮。
#   * 字段面：8 个全局键 + 四层各 8 个公共键 + 8 个分层专属键，合计 48；
#     公共键只由 mem_lines() 一处生成，CLI 经公共解析层逐字渲染。
#   * 鉴权：上报与占用校验调用者自身的 Agent 身份与 SEND 权限，配额校验
#     SUPERVISE；历史与留存不得落内核（属 logger_d）。
#   * 出树构建通过 -Wall -Wextra -Werror；文本/JSON/TOP 与失败路径。
#
# 退出码: 0 通过；1 契约或构建不成立；5 参数或目录不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/apt.sh
. "${ROOT}/ci/lib/apt.sh"

SRC_DIR="${ROOT}/system/obs/mem"
SRC="${SRC_DIR}/airy_mem.c"
CMAKE="${SRC_DIR}/CMakeLists.txt"
COMMON_DIR="${ROOT}/system/obs/common"
COMMON="${COMMON_DIR}/obs_view.c"
UAPI="${ROOT}/kernel/include/uapi/linux/airymax/mem.h"
KDIR="${ROOT}/kernel/kernel/obs"
KSRC="${KDIR}/airy_mem.c"
KHDR="${KDIR}/airy_mem.h"
KTEST="${KDIR}/airy_mem_test.c"
KINIT="${KDIR}/airy_obs_core.c"
TAP="${ROOT}/tools/tap/kunit-tap-baseline.tap"

need_dir --src "${SRC_DIR}"
need_file "${SRC}" "记忆入口源码"
need_file "${CMAKE}" "构建脚本"
need_dir --src "${COMMON_DIR}"
need_file "${COMMON}" "公共解析层"
need_file "${UAPI}" "契约头"
need_file "${KSRC}" "内核计量源码"
need_file "${KHDR}" "内核内部头"
need_file "${KTEST}" "KUnit 用例"
need_file "${TAP}" "TAP 基线"

chk_files()
{
	local got

	group "目录与头注释"
	got="$(find "${SRC_DIR}" -mindepth 1 -maxdepth 1 -printf '%f\n' |
		LC_ALL=C sort | tr '\n' ' ')"
	if [ "${got}" != 'CMakeLists.txt airy_mem.c ' ]; then
		bad "目录内容不符: ${got}"
	fi
	[ "$(sed -n '1p' "${SRC}")" = '// SPDX-License-Identifier: GPL-2.0-only' ] ||
		bad "airy_mem.c: 首行缺少 SPDX"
	sed -n '1,8p' "${SRC}" | grep -q 'Copyright' ||
		bad "airy_mem.c: 缺少版权行"
	have 'obs_view\.c' "${CMAKE}" "构建未编入公共解析层"
	have 'SPDX-License-Identifier' "${CMAKE}" "CMakeLists 缺少 SPDX"
	have 'AIRY_HDR_ROOT' "${CMAKE}" "CMakeLists 未使用导出头"
	endgroup
}

chk_uapi()
{
	group "UAPI 契约"
	have '#define AIRY_MEM_ABI[[:space:]]+1' "${UAPI}" "ABI 版本未定义"
	have '#define AIRY_MEM_LEVELS[[:space:]]+4U' "${UAPI}" "层数不是 4"
	have 'AIRY_MEM_WINDOW_NS[[:space:]]+60000000000ULL' "${UAPI}" \
		"观测窗口不是 60 秒"
	have 'AIRY_MEM_SOFT_PPM[[:space:]]+800000U' "${UAPI}" \
		"软阈值不是 80%"
	have 'AIRY_MEM_DEGRADE_PPM[[:space:]]+950000U' "${UAPI}" \
		"降级阈值不是 95%"
	have 'AIRY_MEM_IOC_REPORT' "${UAPI}" "缺少 REPORT"
	have 'AIRY_MEM_IOC_GAUGE' "${UAPI}" "缺少 GAUGE"
	have 'AIRY_MEM_IOC_QUOTA' "${UAPI}" "缺少 QUOTA"
	have 'AIRY_MEM_IOC_QUERY' "${UAPI}" "缺少 QUERY"
	have '_Static_assert\(sizeof\(struct airy_mem_delta\)' "${UAPI}" \
		"缺少增量布局断言"
	have '_Static_assert\(sizeof\(struct airy_mem_gauge\)' "${UAPI}" \
		"缺少占用布局断言"
	have '_Static_assert\(sizeof\(struct airy_mem_quota\)' "${UAPI}" \
		"缺少配额布局断言"
	have '_Static_assert\(sizeof\(struct airy_mem_stats\)' "${UAPI}" \
		"缺少快照布局断言"
	have '__u8 pad\[3\];' "${UAPI}" "增量与占用缺少显式对齐字段"
	endgroup
}

chk_kern()
{
	group "内核接线"
	have 'airy_mem\.o' "${KDIR}/Kbuild" "Kbuild 未编入计量对象"
	have 'airy_mem_test\.o' "${KDIR}/Kbuild" "Kbuild 未编入用例"
	have 'airy_mem_init' "${KINIT}" "观测量初始化未接线"
	have 'kunit_test_suites\(&airy_mem_suite\)' "${KTEST}" "用例未注册"
	have '\.name = "airy_mem"' "${KTEST}" "suite 名称不是 airy_mem"
	have '^ok 37 airy_mem$' "${TAP}" "TAP 基线未登记新 suite"
	have 'proc_create_single_data\(MEM_FILE, 0444' "${KSRC}" \
		"快照节点不是只读"
	have 'airy_obs_agent_dir\(a->agent_id, true\)' "${KSRC}" \
		"未走共享 Agent 目录"
	have 'airy_obs_ppm\(' "${KSRC}" "比例未走统一 ppm 换算"
	have 'airy_obs_self\(' "${KSRC}" "上报未走统一身份校验"
	have 'airy_obs_supervise\(' "${KSRC}" "配额未走统一监督校验"
	have 'CAP_SYS_PTRACE' "${KINIT}" "缺少监督进程通道"
	endgroup
}

chk_parity()
{
	local got lit p n

	group "字段面对齐"
	got="$(sed -n 's/.*%s_\([a-z0-9_]*\):.*/\1/p' "${KSRC}" |
		LC_ALL=C sort -u | tr '\n' ' ')"
	if [ "${got}" != 'access_total evict_ppm evict_total hit_ppm hit_total lat_avg_ns lat_p99_ns used_kb ' ]; then
		bad "四层公共键不符: ${got}"
	fi
	for p in l1 l2 l3 l4; do
		have "mem_lines\\(m, \"${p}\"" "${KSRC}" "缺少 ${p} 层输出"
	done

	lit="$( { grep -o 'mem_row(m, "[a-z0-9_]*"' "${KSRC}" |
			sed 's/.*"\([a-z0-9_]*\)"/\1/'
		 grep -o 'seq_printf(m, "[a-z0-9_]*: ' "${KSRC}" |
			sed 's/.*"\([a-z0-9_]*\): /\1/' ; } |
		LC_ALL=C sort -u)"
	n="$(printf '%s\n' "${lit}" | wc -l)"
	if [ "${n}" -ne 16 ]; then
		bad "字面键数不是 16（全局 8 + 分层专属 8），实际 ${n}"
	fi
	if [ "$((n + 4 * 8))" -ne 48 ]; then
		bad "字段面合计不是 48"
	fi
	for p in agent_id used_kb quota_kb alert_state usage_ppm \
		 l2_page_in_total l3_read_bytes l4_pattern_drop_total; do
		printf '%s\n' "${lit}" | grep -qx "${p}" ||
			bad "缺少键 ${p}"
	done

	have 'obs_parse\(' "${COMMON}" "公共层未提供行解析"
	have 'obs_json\(' "${COMMON}" "公共层未提供 JSON 渲染"
	have 'obs_json\(' "${SRC}" "入口未走公共 JSON 渲染"
	have 'obs_scan\(' "${SRC}" "入口未走公共目录扫描"

	n="$(grep -o '"[a-z0-9_]*_kb"' "${SRC}" | LC_ALL=C sort -u | wc -l)"
	if [ "${n}" -ne 1 ]; then
		bad "入口出现 ${n} 个占用量字面量，应只有排序用的一处"
	fi
	for p in $(grep -o '"[a-z0-9_]*_kb"' "${SRC}" |
		   LC_ALL=C sort -u | tr -d '"'); do
		printf '%s\n' "${lit}" | grep -qx "${p}" ||
			bad "内核未输出入口使用的键 ${p}"
	done
	endgroup
}

chk_scope()
{
	group "边界与鉴权"
	deny '/var/log/airy' "${KSRC}" "内核负责历史留存"
	deny '/var/log/airy' "${SRC}" "入口负责历史留存"
	deny '/var/log/airy' "${COMMON}" "公共层负责历史留存"
	deny 'system\(' "${SRC}" "经 shell 启动外部命令"
	deny 'popen\(' "${SRC}" "经管道 shell 取输出"
	deny '"/sys/' "${SRC}" "工具直接写 sysfs"
	deny 'sched_setscheduler' "${SRC}" "工具干预调度策略"
	endgroup
}

deps()
{
	if command -v cmake >/dev/null 2>&1 &&
	   command -v cc >/dev/null 2>&1 &&
	   command -v make >/dev/null 2>&1; then
		return 0
	fi
	note "安装记忆入口构建依赖"
	apt_sync && apt_install_min cmake gcc libc6-dev make
}

build_tree()
{
	local dir="$1" log="$1/build.log"

	if ! deps; then
		bad "构建依赖不可用"
		return 1
	fi
	if ! make -C "${ROOT}/kernel" O="${dir}/kobj" headers_install \
		INSTALL_HDR_PATH="${dir}/hdr" >"${log}" 2>&1; then
		bad "UAPI 头导出失败"
		tail -n 20 "${log}" >&2
		return 1
	fi
	if ! cmake -S "${SRC_DIR}" -B "${dir}/b" -DCMAKE_BUILD_TYPE=Release \
		-DAIRY_HDR_ROOT="${dir}/hdr/include" >>"${log}" 2>&1; then
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

# lvl_kb AGENT LEVEL —— 夹具里一层的占用量；总和与全局 used_kb 相符。
lvl_kb()
{
	case "$1:$2" in
	7:l1|7:l2) echo 256 ;;
	7:l3|7:l4) echo 64 ;;
	12:l1|12:l2) echo 32 ;;
	12:l3|12:l4) echo 16 ;;
	esac
}

# mkfx DIR —— 造两个 Agent 的快照夹具，字段顺序与内核输出逐字一致。
mkfx()
{
	local dir="$1" a l

	for a in 7 12; do
		mkdir -p "${dir}/agents/${a}"
		{
			echo "agent_id: ${a}"
			echo "report_time_ns: 1000000000"
			echo "window_duration_s: 60"
			if [ "${a}" = 7 ]; then
				echo "used_kb: 640"
			else
				echo "used_kb: 96"
			fi
			echo "quota_kb: 4194304"
			echo "usage_ppm: 152"
			echo "alert_state: NORMAL"
			echo "updated_ns: 1000000000"
			for l in l1 l2 l3 l4; do
				echo "${l}_access_total: 10"
				echo "${l}_hit_total: 8"
				echo "${l}_hit_ppm: 800000"
				echo "${l}_lat_avg_ns: 100"
				echo "${l}_lat_p99_ns: 900"
				echo "${l}_evict_total: 1"
				echo "${l}_evict_ppm: 90909"
				echo "${l}_used_kb: $(lvl_kb "${a}" "${l}")"
				case "${l}" in
				l2)
					echo "l2_fill_avg_ns: 250"
					echo "l2_page_in_total: 4"
					echo "l2_page_out_total: 1"
					;;
				l3)
					echo "l3_read_bytes: 4096"
					echo "l3_write_bytes: 1024"
					;;
				l4)
					echo "l4_decomp_avg_ns: 120"
					echo "l4_pattern_add_total: 2"
					echo "l4_pattern_drop_total: 1"
					;;
				esac
			done
		} >"${dir}/agents/${a}/memory_stats"
	done
}

# run_rc OUT CMD... —— 捕获退出码到 RC；输出重定向到 OUT。
RC=0
run_rc()
{
	local out="$1"

	shift
	"$@" >"${out}" 2>&1
	RC=$?
}

# chk_cli BIN DIR —— 文本透传、JSON、TOP 与失败路径。
chk_cli()
{
	local bin="$1" fx="$2/fx" out="$2/cli.out"

	mkfx "${fx}"

	run_rc "${out}" "${bin}" --root "${fx}" show 7
	[ "${RC}" -eq 0 ] || bad "show 7 未退 0"
	grep -q '^agent_id: 7$' "${out}" || bad "show 7 未透出内核快照"
	grep -q '^l3_read_bytes: 4096$' "${out}" || bad "show 7 缺分层专属键"

	run_rc "${out}" "${bin}" --root "${fx}" show 7 --json
	[ "${RC}" -eq 0 ] || bad "show --json 未退 0"
	grep -q '"used_kb":640' "${out}" || bad "JSON 占用总量不对"
	grep -q '"alert_state":"NORMAL"' "${out}" || bad "JSON 缺告警档位"

	run_rc "${out}" "${bin}" --root "${fx}" top -n 1
	[ "${RC}" -eq 0 ] || bad "top 未退 0"
	[ "$(sed -n '2p' "${out}")" = "$(printf '7\t640')" ] ||
		bad "top 未按占用降序取首行"

	run_rc "${out}" "${bin}" --root "${fx}" top --json
	[ "${RC}" -eq 0 ] || bad "top --json 未退 0"
	grep -q '^\[{"agent_id":7,"used_kb":640},' "${out}" ||
		bad "top --json 未按占用排序"

	run_rc "${out}" "${bin}" --root "${fx}" show 9
	[ "${RC}" -eq 3 ] || bad "缺失快照未退 3"

	run_rc "${out}" "${bin}" --root "${fx}" show
	[ "${RC}" -eq 2 ] || bad "缺参数未退 2"

	run_rc "${out}" "${bin}" --dev "${fx}/absent" quota 7 1024
	[ "${RC}" -eq 4 ] || bad "缺设备时配额未退 4"

	run_rc "${out}" "${bin}" --dev "${fx}/absent" gauge 7 1 128
	[ "${RC}" -eq 4 ] || bad "缺设备时占用未退 4"

	run_rc "${out}" "${bin}" --root "${fx}" gauge 7 5 128
	[ "${RC}" -eq 2 ] || bad "越界层号未退 2"

	run_rc "${out}" "${bin}" --dev "${fx}/absent" report 7 1 1 1
	[ "${RC}" -eq 2 ] || bad "report 字段不足未退 2"
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
		if [ -x "${tmp}/b/airy-mem" ]; then
			chk_cli "${tmp}/b/airy-mem" "${tmp}"
		else
			bad "缺少 airy-mem 产物"
		fi
	fi
	rm -rf "${tmp}"
	endgroup
}

chk_files
chk_uapi
chk_kern
chk_parity
chk_scope
chk_build

if [ "${FAILS}" -ne 0 ]; then
	err "记忆计量门禁失败 ${FAILS} 项"
	exit 1
fi

say "记忆计量门禁通过"
exit 0
