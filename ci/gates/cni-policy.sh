#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/cni-policy.sh — CNI 链式插件与网络策略（P-24 / CA-087）的
# NetConf 归一、判定矩阵、注册记录与接线门禁
#
# 用法:
#   ci/gates/cni-policy.sh
#
# 断言链：
#   1. 插件源码、测试、夹具与文档齐全并带 SPDX；cloudnative 已接入
#      cni 子目录。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. version 输出 CNI 1.0.0 与支持版本集合。
#   4. ADD 原样回传 prevResult 并落注册记录；CHECK 命中；DEL 幂等清理。
#   5. 缺 prevResult 与不支持版本退 3，命令非法退 2。
#   6. 离线 eval 的入站对端／端口与出站例外／端口判定逐条符合设计。
#   7. spec 装箱与 cloudnative-verify 作业接线成立，预算登记 4 个作业。
#   8. 源码树无构建残留、占位标记与本地绝对路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

CN="${ROOT}/cloudnative"
CNI="${CN}/cni"
FIX="${CNI}/tests/fixtures"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/cni/airy-cni"
RUN="${WORK}/run"

# cni_run CMD —— 在隔离目录里发一次 CNI 协议调用，记录写入相对 stateDir。
cni_run()
{
	env -C "${RUN}" CNI_COMMAND="$1" CNI_CONTAINERID=gate01 \
	    CNI_NETNS=/var/run/netns/gate CNI_IFNAME=eth0 \
	    "${BIN}" <"${FIX}/netconf.json" >"${WORK}/out.$1" 2>&1
}

# cni_verdict WANT REASON ARG... —— 复算一条流的判定与理由标记。
cni_verdict()
{
	local want="$1" reason="$2" want_rc=6 rc=0 out

	[ "${want}" = allow ] && want_rc=0
	shift 2
	out="$("${BIN}" eval --conf "${FIX}/netconf.json" "$@" 2>&1)" || rc=$?
	[ "${rc}" -eq "${want_rc}" ] || bad "eval $* 期望 ${want}，实退 ${rc}"
	have "reason=${reason}$" <(printf '%s\n' "${out}") \
	    "eval $* 理由应为 ${reason}"
}

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "cni-policy: 布局"
for name in cni/cni_api.h cni/cni_conf.c cni/cni_policy.c \
    cni/cni_state.c cni/cni_main.c cni/CMakeLists.txt \
    cni/tests/cni_test.c cni/tests/fixtures/netconf.json \
    cni/tests/fixtures/minimal.json cni/tests/fixtures/bad-version.json \
    cni/tests/fixtures/no-prev.json Documentation/cni.md; do
    [ -f "${CN}/${name}" ] || bad "缺少 cloudnative/${name}"
done
have '^add_subdirectory\(cni\)$' "${CN}/CMakeLists.txt" \
    "cloudnative 未接入 cni 子目录"
for src in "${CNI}"/*.c "${CNI}"/*.h "${CNI}"/tests/*.c; do
    have '^/\* SPDX-License-Identifier: GPL-2\.0-only \*/$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
have '^# SPDX-License-Identifier: GPL-2\.0-only$' "${CNI}/CMakeLists.txt" \
    "缺少 GPL-2.0-only SPDX: ${CNI}/CMakeLists.txt"
endgroup

# ─── 2. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "cni-policy: 出树构建与测试"
airy_build_test "${CN}" "${BUILD}" cloudnative && built=1
endgroup

# ─── 3. 协议版本 ─────────────────────────────────────────────────────
group "cni-policy: 版本"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" version 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "version 退出码 ${rc}，期望 0"
    have '^\{"cniVersion":"1\.0\.0","supportedVersions":' \
        <(printf '%s\n' "${out}") "版本输出不符"
    have '"0\.4\.0","0\.3\.1"' <(printf '%s\n' "${out}") \
        "支持版本集合不完整"
fi
endgroup

# ─── 4. ADD / CHECK / DEL 与注册记录 ─────────────────────────────────
group "cni-policy: 注册记录"
if [ "${built}" -eq 1 ]; then
    mkdir -p "${RUN}"

    rc=0
    cni_run ADD || rc=$?
    [ "${rc}" -eq 0 ] || bad "ADD 退出码 ${rc}，期望 0"
    have '"ips"' "${WORK}/out.ADD" "ADD 未回传 prevResult 的地址"
    have '10\.1\.0\.5/24' "${WORK}/out.ADD" "ADD 未保留容器地址"
    have '"routes"' "${WORK}/out.ADD" "ADD 未原样回传路由"
    have '"defaultDeny":true' "${RUN}/state/gate01.json" \
        "注册记录缺默认拒绝"
    have '"allowedPeers":\["cognition-agent","planner-agent"\]' \
        "${RUN}/state/gate01.json" "注册记录缺放行对端"
    have '"ingressPorts":\[7766,7767\]' "${RUN}/state/gate01.json" \
        "注册记录缺 AgentsIPC 端口"
    have '"egressExceptions":\["10\.0\.0\.0/8","169\.254\.0\.0/16"\]' \
        "${RUN}/state/gate01.json" "注册记录缺出站例外"

    rc=0
    cni_run CHECK || rc=$?
    [ "${rc}" -eq 0 ] || bad "CHECK 退出码 ${rc}，期望 0"

    rc=0
    cni_run DEL || rc=$?
    [ "${rc}" -eq 0 ] || bad "DEL 退出码 ${rc}，期望 0"
    [ ! -e "${RUN}/state/gate01.json" ] || bad "DEL 未清理注册记录"

    rc=0
    cni_run DEL || rc=$?
    [ "${rc}" -eq 0 ] || bad "重复 DEL 退出码 ${rc}，期望幂等 0"

    rc=0
    ( cd "${RUN}" && CNI_COMMAND=ADD CNI_CONTAINERID=gate02 \
      CNI_NETNS=/var/run/netns/gate CNI_IFNAME=eth0 \
      "${BIN}" <"${FIX}/no-prev.json" >/dev/null 2>&1 ) || rc=$?
    [ "${rc}" -eq 3 ] || bad "缺 prevResult 退出码 ${rc}，期望 3"

    rc=0
    ( cd "${RUN}" && CNI_COMMAND=ADD CNI_CONTAINERID=gate02 \
      CNI_NETNS=/var/run/netns/gate CNI_IFNAME=eth0 \
      "${BIN}" <"${FIX}/bad-version.json" >/dev/null 2>&1 ) || rc=$?
    [ "${rc}" -eq 3 ] || bad "不支持版本退出码 ${rc}，期望 3"

    rc=0
    ( cd "${RUN}" && CNI_COMMAND=NOPE "${BIN}" </dev/null >/dev/null 2>&1 ) \
        || rc=$?
    [ "${rc}" -eq 2 ] || bad "非法命令退出码 ${rc}，期望 2"
fi
endgroup

# ─── 5. 离线判定矩阵 ─────────────────────────────────────────────────
group "cni-policy: 判定矩阵"
if [ "${built}" -eq 1 ]; then
    cni_verdict allow peer-port --dir ingress --peer cognition-agent --port 7766
    cni_verdict deny peer-denied --dir ingress --peer evil-agent --port 7766
    cni_verdict deny port-denied --dir ingress --peer cognition-agent --port 80
    cni_verdict allow egress-port --dir egress --port 443 --dst 8.8.8.8
    cni_verdict deny egress-exception --dir egress --port 443 --dst 10.1.2.3
    cni_verdict deny egress-exception --dir egress --port 443 \
        --dst 169.254.169.254
    cni_verdict deny egress-denied --dir egress --port 80 --dst 8.8.8.8
    cni_verdict allow egress-port --dir egress --port 53 --dst 8.8.8.8
fi
endgroup

# ─── 6. 常量与设计基线 ───────────────────────────────────────────────
group "cni-policy: 设计基线"
have 'define CNI_SPEC_VER[[:space:]]+"1\.0\.0"' "${CNI}/cni_api.h" \
    "插件版本不是 1.0.0"
for port in 7766 7767; do
    have "port\[[01]\][[:space:]]*=[[:space:]]*${port}" "${CNI}/cni_conf.c" \
        "缺省入站端口缺 ${port}"
done
have 'eport\[0\][[:space:]]*=[[:space:]]*53' "${CNI}/cni_conf.c" \
    "缺省出站端口缺 DNS 53"
for cidr in '10\.0\.0\.0/8' '169\.254\.0\.0/16'; do
    have "\"${cidr}\"" "${CNI}/cni_conf.c" "缺省出站例外缺 ${cidr}"
done
endgroup

# ─── 7. 打包与流水线接线 ─────────────────────────────────────────────
group "cni-policy: 安装接线"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/cloudnative-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
for pattern in \
    'target airy-crdctl airy-shim airy-cni' \
    'cloudnative-build/cni/airy-cni' \
    '%\{_sbindir\}/airy-cni$'; do
    have "${pattern}" "${SPEC}" "cni-policy: spec 缺少 ${pattern}"
done
have 'cni-policy\.sh' "${FLOW}" "cni-policy: workflow 未监听门禁"
have '^  cni:' "${FLOW}" "cni-policy: workflow 未新增 cni 作业"
have 'ci/ci\.sh cni-policy' "${FLOW}" "cni-policy: workflow 未调用门禁"
have '^cloudnative-verify\.yml[[:space:]]+0[[:space:]]+4$' "${BUDGET}" \
    "cni-policy: 预算未把 cloudnative-verify 登记为 4 个作业"
endgroup

# ─── 8. 源码树卫生 ───────────────────────────────────────────────────
group "cni-policy: 源码树卫生"
leftover="$(find "${CN}" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name cni_test -o -name airy-cni \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for src in "${CNI}"/*.c "${CNI}"/*.h "${CNI}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "cni-policy 门禁失败 ${FAILS} 项"
say "cni-policy 门禁通过"
