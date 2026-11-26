#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/agent-ops.sh — Agent 运维（P-16 / CA-053）的布局、状态机、
# 健康分、日志权限与打包接线门禁
#
# 用法:
#   ci/gates/agent-ops.sh
#
# 断言链：
#   1. 运维包、入口与测试齐全，源码带 SPDX 头。
#   2. 全部模块可编译，单元测试覆盖模型、存储、健康、日志与命令行。
#   3. 失败路径退出码可归因：参数错 2、状态或存储不允许 3、权限拒绝 4。
#   4. 8 态生命周期可从创建走到终止，元数据归档到同级 archive 目录。
#   5. 日志读取与归档受 airy-log 组约束，环境变量可注入组名。
#   6. 13 项默认配置可被配置文件覆盖，坏值退出码 3。
#   7. 六因素健康分缺数据时降权并标 UNKNOWN。
#   8. migrate/upgrade 固定声称本版不提供并退出 3。
#   9. CMake、spec、workflow 接线成立，包内不得残留桩标记或本机路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SYS="${ROOT}/system"
PKG="${SYS}/ops/agent"
ENTRY="${SYS}/bin/airy-agent"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/ops-verify.yml"

need_cmd python3

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# 每个场景独立的存储、日志与归档根，避免编号与状态互相污染。
AR="${WORK}/agents"
LR="${WORK}/logs"
LA="${WORK}/log-archive"

agent() {
    python3 "${ENTRY}" --root "${AR}" --log-root "${LR}" \
        --log-archive "${LA}" "$@"
}

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "agent-ops: 布局"
for name in __init__.py __main__.py model.py config.py store.py tune.py \
    logs.py health.py cli.py; do
    [ -f "${PKG}/${name}" ] || bad "缺少模块 ${name}"
done
for test in model store health logs cli; do
    [ -f "${SYS}/tests/ops/test_agent_${test}.py" ] \
        || bad "缺少测试 test_agent_${test}.py"
done
[ -x "${ENTRY}" ] || bad "入口不可执行: bin/airy-agent"
for src in "${PKG}"/*.py "${ENTRY}"; do
    have '^# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0' \
        "${src}" "缺少 SPDX 标识: ${src}"
done
endgroup

# ─── 2. 编译与单元测试 ───────────────────────────────────────────────
group "agent-ops: 编译与单元测试"
if ! python3 -m py_compile "${PKG}"/*.py; then
    bad "Agent 运维包无法编译"
fi
if ! (cd "${SYS}" && python3 -m unittest discover \
    -s tests/ops -p 'test_agent_*.py'); then
    bad "Agent 运维单元测试未通过"
fi
endgroup

# ─── 3. 失败路径退出码 ───────────────────────────────────────────────
group "agent-ops: 失败路径"
rc=0
agent create --name bot --template chat --memory-l1 0MB >/dev/null 2>&1 \
    || rc=$?
[ "${rc}" -eq 2 ] || bad "坏内存参数退出码 ${rc}，期望 2"

rc=0
agent status 99 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "缺 Agent 退出码 ${rc}，期望 3"

agent create --name bot --template chat >/dev/null 2>&1 \
    || bad "登记 Agent 失败"
rc=0
agent stop 1 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "INACTIVE 直接 stop 退出码 ${rc}，期望 3"

rc=0
AIRY_AGENT_GROUPS=users agent log 1 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "缺 airy-log 组退出码 ${rc}，期望 4"
endgroup

# ─── 4. 生命周期与健康分 ─────────────────────────────────────────────
group "agent-ops: 生命周期"
AR="${WORK}/life"
out="$(agent create --name bot --template chat --capabilities "fs.read")" \
    || bad "创建失败"
ident="${out%% *}"
have '^[0-9]+ bot INACTIVE$' <(printf '%s\n' "${out}") "初始态不是 INACTIVE"
have '^[0-9]+ RUNNING$' <(agent start "${ident}") "start 未进入 RUNNING"
have '^[0-9]+ BLOCKED$' <(agent pause "${ident}") "pause 未进入 BLOCKED"
have '^[0-9]+ RUNNING$' <(agent resume "${ident}") "resume 未回到 RUNNING"
agent tune "${ident}" --sched-runtime 5ms --memory-l1 128MB \
    --memory-l2 512MB >/dev/null || bad "tune 失败"
have '"l1_mb":128' <(agent status "${ident}" --json) "tune 未落 L1"
agent token "${ident}" budget 5000 >/dev/null || bad "token budget 失败"
have 'consumed=0 remaining=5000' <(agent token "${ident}" status) \
    "token status 未反映预算"
have '"state":"RUNNING"' <(agent list --json) "list 未列出运行中的 Agent"
have 'grade=UNKNOWN' <(agent health "${ident}") "缺数据时未标 UNKNOWN"
have '^[0-9]+ STOPPED$' <(agent stop "${ident}") "stop 未进入 STOPPED"
have '^[0-9]+ RUNNING$' <(agent restart "${ident}") "restart 未回到 RUNNING"
detail="$(agent terminate "${ident}")" || bad "terminate 失败"
have 'DEAD archived=' <(printf '%s\n' "${detail}") "terminate 未归档"
ls -d "${WORK}/life.archive/${ident}"-* >/dev/null 2>&1 \
    || bad "归档目录未生成"
rc=0
agent status "${ident}" >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "归档后查状态退出码 ${rc}，期望 3"
endgroup

# ─── 5. 日志读取、权限与归档 ─────────────────────────────────────────
group "agent-ops: 日志"
AR="${WORK}/logstore"
LR="${WORK}/logstore-logs"
LA="${WORK}/logstore-archive"
out="$(agent create --name bot --template chat)" || bad "创建失败"
ident="${out%% *}"
mkdir -p "${LR}/${ident}"
printf 'alpha\nbeta\n' > "${LR}/${ident}/agent.log"
have '^alpha$' <(AIRY_AGENT_GROUPS=airy-log agent log "${ident}") \
    "日志未读出首行"
have '^beta$' <(AIRY_AGENT_GROUPS=airy-log agent log "${ident}") \
    "日志未读出次行"
rc=0
AIRY_AGENT_GROUPS=users agent log "${ident}" >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "日志越权退出码 ${rc}，期望 4"
touch -d '60 days ago' "${LR}/${ident}/agent.log"
have 'archived=1' <(AIRY_AGENT_GROUPS=airy-log agent log archive "${ident}") \
    "归档未搬走旧日志"
[ -f "${LA}/${ident}/agent.log.gz" ] || bad "归档未生成 .log.gz"
endgroup

# ─── 6. 配置覆盖 ─────────────────────────────────────────────────────
group "agent-ops: 配置"
AR="${WORK}/conf"
CONF="${WORK}/agents.conf"
printf 'default_l1_mb = 64\nairy_agent.default_token_budget: 5000\n' \
    > "${CONF}"
out="$(python3 "${ENTRY}" --root "${AR}" --conf "${CONF}" \
    create --name cfg --template chat)" || bad "带配置创建失败"
ident="${out%% *}"
have '"l1_mb":64' <(python3 "${ENTRY}" --root "${AR}" --conf "${CONF}" \
    status "${ident}" --json) "配置未覆盖默认 L1"
have '"total_budget":5000' <(python3 "${ENTRY}" --root "${AR}" \
    --conf "${CONF}" status "${ident}" --json) "配置未覆盖令牌预算"
printf 'default_l1_mb = abc\n' > "${WORK}/bad.conf"
rc=0
python3 "${ENTRY}" --root "${AR}" --conf "${WORK}/bad.conf" \
    create --name x --template chat >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "坏配置退出码 ${rc}，期望 3"
endgroup

# ─── 7. 迁移与升级边界 ───────────────────────────────────────────────
group "agent-ops: 迁移与升级"
for verb in migrate upgrade; do
    rc=0
    text="$(agent "${verb}" 1 2>&1 >/dev/null)" || rc=$?
    [ "${rc}" -eq 3 ] || bad "${verb} 退出码 ${rc}，期望 3"
    printf '%s\n' "${text}" | grep -q '本版单节点不提供' \
        || bad "${verb} 未声明本版边界"
done
endgroup

# ─── 8. 安装接线与桩标记 ─────────────────────────────────────────────
group "agent-ops: 安装接线"
have 'bin/airy-agent' "${SYS}/CMakeLists.txt" "CMake 未安装入口"
have 'ops/agent' "${SYS}/CMakeLists.txt" "CMake 未安装运维包"
have 'Source101' "${SPEC}" "spec 未登记入口源"
have 'Source102' "${SPEC}" "spec 未登记运维包源"
have 'source-bundle ../../system/ops/agent' "${SPEC}" \
    "spec-lint 未声明运维包"
have 'airy-agent' "${SPEC}" "spec 未安装入口"
have '%\{python3_sitelib\}/agent' "${SPEC}" "spec 未打包运维模块"
have 'agent-ops\.sh' "${FLOW}" "workflow 未监听门禁脚本"
have '^  agent-verify:' "${FLOW}" "workflow 未新增 agent 作业"
for src in "${PKG}"/*.py "${ENTRY}"; do
    deny 'NotImplementedError|TODO|FIXME|breakpoint\(' "${src}" \
        "残留桩标记: ${src}"
    deny '/home/' "${src}" "残留本机绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "agent-ops 门禁失败 ${FAILS} 项"
say "agent-ops 门禁通过"
