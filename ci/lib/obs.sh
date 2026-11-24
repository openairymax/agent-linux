#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/obs.sh — 观测面门禁的公共骨架（记忆、token、eBPF、perf 共用）
#
# 用法:
#   . "${ROOT}/ci/lib/obs.sh"          # 需先加载 log.sh 与 apt.sh
#   OBS_TITLE="记忆计量" OBS_LABEL="记忆入口"
#   OBS_BIN="airy-mem" OBS_CMDS=(cmake cc make)
#   OBS_PKGS=(cmake gcc libc6-dev make) OBS_HDRS=1
#   chk_out() { chk_view "${1}/b/${OBS_BIN}" "$1"; }
#   obs_gate chk_files chk_uapi chk_kern
#
# 四道门禁原先各自复制一份「查依赖 → 导 UAPI 头 → 配 cmake → 编译 → 建临时
# 目录 → 清理 → 汇总」的骨架，同一失败点在四份实现里各写一遍，改一处必漏
# 三处；目录与头注释断言同样四份。骨架收在这里，门禁只声明差异与产物断言。
#
# 调用方约定的变量：
#   OBS_TITLE  门禁名（汇总语用）
#   OBS_LABEL  依赖安装提示语
#   OBS_BIN    构建产物名
#   OBS_CMDS   必需命令
#   OBS_PKGS   依赖包
#   OBS_HDRS   1 表示先导出内核 UAPI 头
#   OBS_PROBE  额外探测函数名，可空
# 调用方需定义 chk_out DIR 做产物断言。
# ===========================================================================

OBS_CMDS=()
OBS_PKGS=()
OBS_PROBE=''
OBS_HDRS=0
OBS_TITLE='观测'
OBS_LABEL='观测面'
OBS_BIN=''

# obs_dir DIR EXPECT —— 目录顶层只放声明过的文件，与顺序无关。
obs_dir()
{
	local got

	got="$(find "$1" -mindepth 1 -maxdepth 1 -printf '%f\n' |
		LC_ALL=C sort | tr '\n' ' ')"
	[ "${got}" = "$2" ] || bad "目录内容不符: ${got}"
}

# obs_head FILE NAME —— 源文件首行 SPDX 与首六行版权行。
obs_head()
{
	[ "$(sed -n '1p' "$1")" = '// SPDX-License-Identifier: GPL-2.0-only' ] ||
		bad "$2: 首行缺少 SPDX"
	sed -n '1,6p' "$1" | grep -q 'Copyright' || bad "$2: 缺少版权行"
}

# obs_cmake CMAKE —— 观测工具的构建脚本必须编入公共解析层、声明许可、
# 并使用内核导出头；这三条是四道观测入口共同的前置条件。
obs_cmake()
{
	have 'obs_view\.c' "$1" "构建未编入公共解析层"
	have 'SPDX-License-Identifier' "$1" "CMakeLists 缺少 SPDX"
	have 'AIRY_HDR_ROOT' "$1" "CMakeLists 未使用导出头"
}

# obs_try WANT OUT CMD... —— 校验退出码；输出落到 OUT 供后续断言。
obs_try()
{
	local want="$1" out="$2" rc

	shift 2
	"$@" >"${out}" 2>&1
	rc=$?
	[ "${rc}" -eq "${want}" ] || bad "$* 退出码 ${rc}，期望 ${want}"
}

# obs_ready —— 命令与额外探测齐全才算依赖就位。
obs_ready()
{
	local cmd

	for cmd in "${OBS_CMDS[@]}"; do
		command -v "${cmd}" >/dev/null 2>&1 || return 1
	done
	[ -z "${OBS_PROBE}" ] || "${OBS_PROBE}"
}

# obs_deps —— 依赖不全时一次装齐；装不上由调用方报缺。
obs_deps()
{
	obs_ready && return 0
	note "安装${OBS_LABEL}构建依赖"
	apt_sync && apt_install_min "${OBS_PKGS[@]}"
}

# obs_configure DIR —— 需要时先导出 UAPI 头，再出树配置。
obs_configure()
{
	local dir="$1" log="$1/build.log"
	local -a conf=(-S "${SRC_DIR}" -B "${dir}/b" -DCMAKE_BUILD_TYPE=Release)

	if [ "${OBS_HDRS}" = 1 ]; then
		conf+=(-DAIRY_HDR_ROOT="${dir}/hdr/include")
		if ! make -C "${ROOT}/kernel" O="${dir}/kobj" headers_install \
			INSTALL_HDR_PATH="${dir}/hdr" >"${log}" 2>&1; then
			bad "UAPI 头导出失败"
			tail -n 20 "${log}" >&2
			return 1
		fi
	fi
	if ! cmake "${conf[@]}" >>"${log}" 2>&1; then
		bad "cmake 配置失败"
		tail -n 20 "${log}" >&2
		return 1
	fi
}

# obs_build DIR —— 配置并编译，失败点各留一段日志尾巴。
obs_build()
{
	local dir="$1" log="$1/build.log"

	obs_deps
	if ! obs_ready; then
		bad "构建依赖不可用"
		return 1
	fi
	obs_configure "${dir}" || return 1
	if ! cmake --build "${dir}/b" -j "$(nproc)" >>"${log}" 2>&1; then
		bad "编译失败"
		tail -n 40 "${log}" >&2
		return 1
	fi
}

# obs_tree —— 临时目录出树构建，成功后交给 chk_out 做产物断言。
obs_tree()
{
	local tmp

	group "出树构建"
	tmp="$(mktemp -d)"
	if [ -z "${tmp}" ]; then
		bad "临时目录创建失败"
		endgroup
		return
	fi
	if ! obs_build "${tmp}"; then
		rm -rf "${tmp}"
		endgroup
		return
	fi
	if [ -x "${tmp}/b/${OBS_BIN}" ]; then
		chk_out "${tmp}"
	else
		bad "缺少 ${OBS_BIN} 产物"
	fi
	rm -rf "${tmp}"
	endgroup
}

# obs_gate CHK... —— 依次跑静态检查与出树构建，统一汇总出口。
obs_gate()
{
	local chk

	for chk in "$@"; do
		"${chk}"
	done
	obs_tree
	if [ "${FAILS}" -ne 0 ]; then
		err "${OBS_TITLE}门禁失败 ${FAILS} 项"
		exit 1
	fi
	say "${OBS_TITLE}门禁通过"
	exit 0
}
