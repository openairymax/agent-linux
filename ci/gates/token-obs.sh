#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/token-obs.sh — token 计量（P-11）契约、口径与出树构建门禁
#
# 用法:
#   ci/gates/token-obs.sh
#
# token 计量有两处容易滑走的错位：一是把处置（降级、终止）塞回内核，让观测
# 面变成策略面；二是比例、窗口、预算各算各的，同一个 Agent 的数在 TUI、日志
# 与告警里互相对不上。门禁把「内核只出事实、比例一律 ppm、四类计数互不重叠、
# 用户态只透传不重算」固化成静态判据，再做一次导出头构建与全部命令行路径。
#
# 判据：
#   * 目录只放源码与 CMakeLists，不落产物；两侧首行均为 SPDX。
#   * UAPI 契约：ABI 版本、60 秒/1 小时窗口、三条 ioctl、三处布局断言。
#   * 内核侧：Kbuild 与 core 初始化接线、KUnit suite、TAP 基线棘轮。
#   * 字段面：内核输出 20 个键；CLI 经公共解析层逐字渲染，源码中除排序
#     用的指标名外不得再出现第二份键表。
#   * 鉴权：计量模块一律走公共鉴权入口，由它校验调用者自身的 Agent 身份
#     与 SEND 权限、预算校验 SUPERVISE；历史与留存不得落内核（属 logger_d）。
#   * 出树构建通过 -Wall -Wextra -Werror；文本/JSON/TOP 与三条失败路径。
#
# 退出码: 0 通过；1 契约或构建不成立；5 参数或目录不满足。
# ===========================================================================
# OBS_* 配置变量由 ci/lib/obs.sh 读取，shellcheck 无法跨文件跟踪，
# 故本文件整体关闭「变量未使用」。
# shellcheck disable=SC2034
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/apt.sh
. "${ROOT}/ci/lib/apt.sh"
# shellcheck source=../lib/obs.sh
. "${ROOT}/ci/lib/obs.sh"

SRC_DIR="${ROOT}/system/obs/token"
SRC="${SRC_DIR}/airy_token.c"
CMAKE="${SRC_DIR}/CMakeLists.txt"
COMMON_DIR="${ROOT}/system/obs/common"
COMMON="${COMMON_DIR}/obs_view.c"
UAPI="${ROOT}/kernel/include/uapi/linux/airymax/token.h"
KDIR="${ROOT}/kernel/kernel/obs"
KSRC="${KDIR}/airy_token.c"
KHDR="${KDIR}/airy_token.h"
KTEST="${KDIR}/airy_token_test.c"
KINIT="${KDIR}/airy_obs_core.c"
TAP="${ROOT}/tools/tap/kunit-tap-baseline.tap"

need_dir --src "${SRC_DIR}"
need_file "${SRC}" "token 入口源码"
need_file "${CMAKE}" "构建脚本"
need_dir --src "${COMMON_DIR}"
need_file "${COMMON}" "公共解析层"
need_file "${UAPI}" "契约头"
need_file "${KSRC}" "内核计量源码"
need_file "${KTEST}" "KUnit 用例"
need_file "${KINIT}" "公共鉴权入口"
need_file "${TAP}" "TAP 基线"

chk_files()
{
	group "目录与头注释"
	obs_dir "${SRC_DIR}" 'CMakeLists.txt airy_token.c '
	obs_head "${SRC}" "airy_token.c"
	obs_dir "${COMMON_DIR}" 'obs_view.c obs_view.h '
	obs_head "${COMMON}" "obs_view.c"
	obs_cmake "${CMAKE}"
	endgroup
}

chk_uapi()
{
	group "UAPI 契约"
	have '#define AIRY_TOKEN_ABI[[:space:]]+1' "${UAPI}" "ABI 版本未定义"
	have 'AIRY_TOKEN_WINDOW_NS[[:space:]]+60000000000ULL' "${UAPI}" \
		"观测窗口不是 60 秒"
	have 'AIRY_TOKEN_BUDGET_WINDOW_NS[[:space:]]+3600000000000ULL' "${UAPI}" \
		"预算窗口不是 1 小时"
	have 'AIRY_TOKEN_IOC_REPORT' "${UAPI}" "缺少 REPORT"
	have 'AIRY_TOKEN_IOC_QUERY' "${UAPI}" "缺少 QUERY"
	have 'AIRY_TOKEN_IOC_BUDGET' "${UAPI}" "缺少 BUDGET"
	have '_Static_assert\(sizeof\(struct airy_token_stats\)' "${UAPI}" \
		"缺少快照布局断言"
	have '_Static_assert\(sizeof\(struct airy_token_delta\)' "${UAPI}" \
		"缺少增量布局断言"
	have '_Static_assert\(sizeof\(struct airy_token_budget\)' "${UAPI}" \
		"缺少预算布局断言"
	have '__u32 pad;' "${UAPI}" "增量缺少显式对齐字段"
	have 'AIRY_TOKEN_SOFT_PPM[[:space:]]+800000U' "${UAPI}" \
		"软阈值不是 80%"
	endgroup
}

chk_kern()
{
	group "内核接线"
	have 'airy_token\.o' "${KDIR}/Kbuild" "Kbuild 未编入计量对象"
	have 'airy_token_test\.o' "${KDIR}/Kbuild" "Kbuild 未编入用例"
	have 'airy_tok_add' "${KHDR}" "内部头缺少累加入口"
	have 'airy_token_init' "${KDIR}/airy_obs_core.c" \
		"观测量初始化未接线"
	have 'kunit_test_suites\(&airy_token_suite\)' "${KTEST}" \
		"用例未注册"
	have '^ok 36 airy_token$' "${TAP}" "TAP 基线未登记新 suite"
	have 'proc_create_single_data\(TOK_FILE, 0444' "${KSRC}" \
		"快照节点不是只读"
	have 'airy_obs_agent_dir\(a->agent_id, true\)' "${KSRC}" \
		"未走共享 Agent 目录"
	have 'airy_obs_agent_dir' "${KDIR}/airy_obs_proc.c" \
		"共享 Agent 目录未实现"
	endgroup
}

chk_parity()
{
	local keys k n

	group "字段面对齐"
	keys="$(sed -n 's/.*seq_printf(m, "\([a-z_]*\):.*/\1/p' "${KSRC}" |
		LC_ALL=C sort -u)"
	if [ "$(printf '%s\n' "${keys}" | wc -l)" -ne 20 ]; then
		bad "内核快照键数不是 20"
	fi

	have 'obs_parse\(' "${COMMON}" "公共层未提供行解析"
	have 'obs_json\(' "${COMMON}" "公共层未提供 JSON 渲染"
	have 'obs_json\(' "${SRC}" "入口未走公共 JSON 渲染"
	have 'obs_scan\(' "${SRC}" "入口未走公共目录扫描"

	n="$(grep -o '"[a-z_]*_total"' "${SRC}" | LC_ALL=C sort -u | wc -l)"
	if [ "${n}" -ne 1 ]; then
		bad "入口出现 ${n} 个指标名字面量，应只有排序用的一处"
	fi
	while read -r k; do
		grep -q "\"${k}: " "${KSRC}" || bad "内核未输出入口使用的键 ${k}"
	done < <(grep -o '"[a-z_]*_total"' "${SRC}" |
		LC_ALL=C sort -u | tr -d '"')
	endgroup
}

chk_scope()
{
	group "边界与鉴权"
	have 'airy_obs_self\(' "${KSRC}" "上报未走统一身份校验"
	have 'airy_obs_supervise\(' "${KSRC}" "预算未走统一监督校验"
	have 'airy_agent_id\(current\)' "${KINIT}" "公共鉴权未取调用者身份"
	have 'AIRY_CAP_PERM_SEND' "${KINIT}" "公共鉴权未校验 SEND 权限"
	have 'AIRY_CAP_PERM_SUPERVISE' "${KINIT}" "公共鉴权未校验 SUPERVISE"
	have 'CAP_SYS_PTRACE' "${KINIT}" "缺少监督进程通道"
	have 'airy_tok_ratio' "${KSRC}" "比例未走统一 ppm 换算"
	deny '/var/log/airy' "${KSRC}" "内核负责历史留存"
	deny '/var/log/airy' "${SRC}" "入口负责历史留存"
	deny '/var/log/airy' "${COMMON}" "公共层负责历史留存"
	deny 'system\(' "${SRC}" "经 shell 启动外部命令"
	deny 'popen\(' "${SRC}" "经管道 shell 取输出"
	deny 'system\(' "${COMMON}" "公共层经 shell 启动外部命令"
	deny 'popen\(' "${COMMON}" "公共层经管道 shell 取输出"
	deny '"/sys/' "${SRC}" "工具直接写 sysfs"
	deny 'sched_setscheduler' "${SRC}" "工具干预调度策略"
	endgroup
}

# mkfx DIR —— 造两个 Agent 的快照夹具，字段顺序与内核输出逐字一致。
mkfx()
{
	local dir="$1"

	mkdir -p "${dir}/agents/7" "${dir}/agents/12"
	cat >"${dir}/agents/7/token_stats" <<'EOF'
agent_id: 7
report_time_ns: 1000000000
input_tokens_total: 1000
output_tokens_total: 500
cache_hit_tokens_total: 300
reasoning_tokens_total: 50
total_tokens_total: 1850
billable_tokens_total: 1500
window_tokens_total: 120
window_start_ns: 900000000
window_duration_s: 60
budget_tokens: 100000000
budget_used: 40000000
budget_ratio_ppm: 400000
budget_period_s: 3600
cache_hit_ratio_ppm: 230769
output_input_ratio_ppm: 500000
reasoning_overhead_ppm: 33333
alert_state: NORMAL
updated_ns: 1000000000
EOF
	sed 's/^agent_id: 7$/agent_id: 12/; s/^total_tokens_total: .*/total_tokens_total: 15/' \
		"${dir}/agents/7/token_stats" >"${dir}/agents/12/token_stats"
}

# chk_view BIN DIR —— 文本透传、JSON 与 TOP 的字段面。
chk_view()
{
	local bin="$1" fx="$2/fx" out="$2/cli.out"

	mkfx "${fx}"
	obs_try 0 "${out}" "${bin}" --root "${fx}" show 7
	have '^agent_id: 7$' "${out}" "show 7 未透出内核快照"
	obs_try 0 "${out}" "${bin}" --root "${fx}" show 7 --json
	have '"total_tokens_total":1850' "${out}" "JSON 总数不对"
	have '"alert_state":"NORMAL"' "${out}" "JSON 缺告警档位"
	obs_try 0 "${out}" "${bin}" --root "${fx}" top -n 1
	[ "$(sed -n '2p' "${out}")" = "$(printf '7\t1850')" ] ||
		bad "top 未按总量降序取首行"
	obs_try 0 "${out}" "${bin}" --root "${fx}" top --json
	have '^\[{"agent_id":7,"total_tokens":1850},' "${out}" \
		"top --json 未按总量排序"
}

# chk_fail BIN DIR —— 缺失、缺参、缺设备与越界四类出口码。
chk_fail()
{
	local bin="$1" fx="$2/fx" out="$2/cli.out"

	obs_try 3 "${out}" "${bin}" --root "${fx}" show 9
	obs_try 2 "${out}" "${bin}" --root "${fx}" show
	obs_try 4 "${out}" "${bin}" --dev "${fx}/absent" report 7 1 1 0 0
	obs_try 2 "${out}" "${bin}" --dev "${fx}/absent" budget 7 0
	obs_try 2 "${out}" "${bin}" --dev "${fx}/absent" report 7 99999999 0 0 0
}

chk_out()
{
	chk_view "${1}/b/${OBS_BIN}" "$1"
	chk_fail "${1}/b/${OBS_BIN}" "$1"
}

OBS_TITLE="token 计量"
OBS_LABEL="token 入口"
OBS_BIN="airy-token"
OBS_CMDS=(cmake cc make)
OBS_PKGS=(cmake gcc libc6-dev make)
OBS_HDRS=1

obs_gate chk_files chk_uapi chk_kern chk_parity chk_scope
