#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/station-ops.sh — 开发运维工作站（P-18）的布局、八面板采集、权限
# 排序、审计留痕、渲染尺寸与打包/流水线接线门禁
#
# 用法:
#   ci/gates/station-ops.sh
#
# 断言链：
#   1. 运维包、入口与六份测试齐全，源码带 SPDX 头，函数名不超过 15 字节。
#   2. 全部模块可编译，单元测试全跑通过。
#   3. 八面板从真实 fixture 出数：监控、日志、Agent、告警、事件、记忆、
#      备份、配置逐节读数正确；空根逐节 unavailable 并带原因。
#   4. 命令行：Web/远程选项显式拒绝退出 3，缺源视图退出 3。
#   5. 权限：view 执行 operate 动作退出 4，admin 缺 --yes 退出 4 且留拒绝
#      审计；operate 放行、admin 确认后写入成功审计。
#   6. 配置：越界与非法取值退出 3。
#   7. 渲染帧宽高不越界（文本与 JSON 的快照同源）。
#   8. CMake、spec、workflow、预算与 README 接线成立，包内无桩标记与本机路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SYS="${ROOT}/system"
PKG="${SYS}/ops/devstation"
ENTRY="${SYS}/bin/airy-station"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/station-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
README="${ROOT}/.github/workflows/README.md"

need_cmd python3
need_file "${ENTRY}" 入口
export PYTHONDONTWRITEBYTECODE=1

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
DATA="${WORK}/fixture"
EMPTY="${WORK}/empty"
LOGS="${WORK}/logs"
mkdir -p "${LOGS}" "${EMPTY}"

station() {
    python3 "${ENTRY}" --root "${DATA}" --conf "${WORK}/none.conf" \
        --log-dir "${LOGS}" "$@"
}

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "station-ops: 布局"
for name in __init__.py __main__.py model.py facts.py panel.py render.py \
    tui.py perm.py audit.py config.py parser.py cli.py; do
    [ -f "${PKG}/${name}" ] || bad "缺少模块 ${name}"
done
for part in model facts panel render perm cli; do
    [ -f "${SYS}/tests/ops/test_station_${part}.py" ] \
        || bad "缺少测试 test_station_${part}.py"
done
[ -x "${ENTRY}" ] || bad "入口不可执行: bin/airy-station"
for src in "${PKG}"/*.py "${ENTRY}"; do
    have '^# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0' \
        "${src}" "缺少 SPDX 标识: ${src}"
done
endgroup

# ─── 2. 编译、函数名与单元测试 ───────────────────────────────────────
group "station-ops: 编译与单元测试"
for src in "${PKG}"/*.py; do
    python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' \
        "${src}" || bad "无法编译: ${src}"
done
python3 - "${PKG}" "${ENTRY}" <<'PY' || bad "函数名超过 15 字节"
import ast
import sys
from pathlib import Path

bad = []
for path in [*Path(sys.argv[1]).glob("*.py"), Path(sys.argv[2])]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if len(node.name.encode()) > 15:
                bad.append("%s:%s" % (path.name, node.name))
if bad:
    print("超长函数名: " + ", ".join(bad))
    raise SystemExit(1)
PY
if ! (cd "${SYS}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
    -s tests/ops -p 'test_station_*.py'); then
    bad "工作站单元测试未通过"
fi
endgroup

# ─── 3. 八面板数据面 ─────────────────────────────────────────────────
group "station-ops: 数据面"
python3 - "${DATA}" "${SYS}" "${WORK}/agent_id" <<'PY' || bad "fixture 构造失败"
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2] + "/tests/ops")
import station_support  # noqa: E402

data = Path(sys.argv[1])
ident = station_support.make(data)
Path(sys.argv[3]).write_text(ident["agent_id"], encoding="utf-8")
print("fixture agent=" + ident["agent_id"])
PY
python3 - "${DATA}" "${EMPTY}" "${SYS}" "${WORK}/none.conf" <<'PY' \
    || bad "八面板读数断言失败"
import sys
from pathlib import Path

data, empty, sysdir, conf = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
sys.path[:0] = [sysdir + "/ops", sysdir + "/services"]
from devstation import panel, render
from devstation.config import load as conf_load
from devstation.model import PANELS

cfg = conf_load(conf)
snap = panel.build_snap(data, "view", cfg)
assert len(PANELS) == 8
for item in PANELS:
    sec = snap.section(item.value)
    assert sec.ready, "%s 未就绪: %s" % (item.value, sec.reason)
print("panels=8 ok")

mon = snap.section("monitor").rows[0]
assert mon["daemon"] == "airymaxmon" and mon["cpu_pct"] == 12.5, mon
logs = snap.section("logs").rows
assert any(row["level"] == "ERROR" for row in logs), logs
agents = snap.section("agents").rows
assert agents and agents[0]["state"] == "INACTIVE", agents
alerts = snap.section("alerts").rows
assert alerts and alerts[0]["alert_id"] == "A-1", alerts
incs = snap.section("incidents").rows
assert incs and incs[0]["incident_id"].startswith("INC-"), incs
mem = snap.section("memory").rows
assert mem and mem[0]["agent"] == "1" and mem[0]["l3_items"] == 1, mem
backup = snap.section("backup").rows
assert backup and backup[0]["backup_id"] == "FULL-20261127-001", backup
config = snap.section("config").rows
assert any(row["key"] == "agent.max_instances" for row in config), config
print("readings=ok")

down = panel.build_snap(empty, "view", cfg)
assert not down.section("backup").ready
assert "缺少备份仓库" in down.section("backup").reason, down.section("backup").reason
assert not down.section("monitor").ready
print("empty=unavailable ok")

frame = render.frame(snap, "monitor", 100, 30)
lines = frame.splitlines()
assert len(lines) == 30, len(lines)
assert all(len(line) <= 100 for line in lines), max(map(len, lines))
small = render.frame(snap, "backup", 8, 4).splitlines()
assert len(small) == 4 and all(len(line) <= 8 for line in small), small
print("frame=30x100/4x8 ok")
PY
endgroup

# ─── 4. 命令行与缺源 ─────────────────────────────────────────────────
group "station-ops: 命令行"
have '"schema": 1' <(station snapshot --json) "snapshot 缺少 schema"
for name in monitor logs agents alerts incidents memory backup config; do
    have "\"${name}\"" <(station snapshot --json) "snapshot 缺少面板 ${name}"
done
have '"status": "unavailable"' <(python3 "${ENTRY}" --root "${EMPTY}" \
    --conf "${WORK}/none.conf" --log-dir "${LOGS}" snapshot --json) \
    "空根未报 unavailable"
have '缺少备份仓库' <(python3 "${ENTRY}" --root "${EMPTY}" \
    --conf "${WORK}/none.conf" --log-dir "${LOGS}" snapshot --json) \
    "空根未给出缺源原因"
out="$(station monitor --once)"
lines="$(printf '%s\n' "${out}" | wc -l)"
[ "${lines}" -eq 30 ] || bad "monitor --once 输出 ${lines} 行，期望 30"
endgroup

# ─── 5. 远程能力显式拒绝 ─────────────────────────────────────────────
group "station-ops: 远程拒绝"
for flag in '--web' '--node n1' '--rpc-port 9000' '--port 8610'; do
    rc=0; station snapshot ${flag} >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "snapshot ${flag} 退出码 ${rc}，期望 3"
done
printf 'devstation.web_enabled = true\n' > "${WORK}/web.conf"
rc=0; station --conf "${WORK}/web.conf" snapshot >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "web_enabled=true 退出码 ${rc}，期望 3"
endgroup

# ─── 6. 权限排序与审计 ───────────────────────────────────────────────
group "station-ops: 权限与审计"
AGENT_ID="$(cat "${WORK}/agent_id")"
rc=0; station --level view agents --id "${AGENT_ID}" --action start \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "view 执行 operate 动作退出码 ${rc}，期望 4"
rc=0; station --level operate agents --id "${AGENT_ID}" --action start \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 0 ] || bad "operate 执行 start 退出码 ${rc}，期望 0"
rc=0; station --level admin memory --action erase --agent 1 --all \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "admin 缺 --yes 退出码 ${rc}，期望 4"
rc=0; station --level admin memory --action erase --agent 1 --all --yes \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 0 ] || bad "admin --yes 擦除退出码 ${rc}，期望 0"
AUDIT="${LOGS}/devstation_audit.log"
[ -f "${AUDIT}" ] || bad "未生成审计文件"
have '"result": "denied"' "${AUDIT}" "审计缺拒绝记录"
have '"result": "ok"' "${AUDIT}" "审计缺成功记录"
have '"action": "agent.start"' "${AUDIT}" "审计缺 Agent 动作"
have '"action": "memory.erase"' "${AUDIT}" "审计缺记忆擦除"
endgroup

# ─── 7. 配置校验 ─────────────────────────────────────────────────────
group "station-ops: 配置"
for line in 'devstation.refresh_interval_s = 0' \
    'devstation.web_port = 70000' 'devstation.color_scheme = blue' \
    'devstation.permission_level = root'; do
    printf '%s\n' "${line}" > "${WORK}/bad.conf"
    rc=0; station --conf "${WORK}/bad.conf" snapshot >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "坏配置 [${line}] 退出码 ${rc}，期望 3"
done
printf 'devstation.default_panel = backup\ndevstation.log_buffer_lines = 42\n' \
    > "${WORK}/good.conf"
have '"panel": "backup"' <(station --conf "${WORK}/good.conf" backup --json) \
    "合法配置未生效"
endgroup

# ─── 8. 安装接线、桩标记与本机路径 ───────────────────────────────────
group "station-ops: 安装接线"
have 'bin/airy-station' "${SYS}/CMakeLists.txt" "CMake 未安装入口"
have 'ops/devstation' "${SYS}/CMakeLists.txt" "CMake 未安装运维包"
have 'Source105' "${SPEC}" "spec 未登记入口源"
have 'Source106' "${SPEC}" "spec 未登记运维包源"
have 'source-bundle ../../system/ops/devstation' "${SPEC}" \
    "spec-lint 未声明运维包"
have 'airy-station' "${SPEC}" "spec 未安装入口"
have '%\{python3_sitelib\}/devstation' "${SPEC}" "spec 未打包运维模块"
[ -f "${FLOW}" ] || bad "缺少流水线 station-verify.yml"
have 'name: station-verify' "${FLOW}" "流水线名不符"
have 'ci/ci\.sh station-ops' "${FLOW}" "流水线未调用门禁"
have 'ci/gates/station-ops\.sh' "${FLOW}" "流水线未监听门禁脚本"
have '^  station-verify:' "${FLOW}" "流水线未新增 station-verify 作业"
have 'station-verify\.yml' "${BUDGET}" "预算未登记 station-verify.yml"
have 'station-verify' "${README}" "README 未同步 station-verify"
for src in "${PKG}"/*.py "${ENTRY}" "${SYS}"'/tests/ops/test_station_'*.py \
    "${SYS}/tests/ops/station_support.py"; do
    deny 'NotImplementedError|TODO|FIXME|breakpoint\(' "${src}" \
        "残留桩标记: ${src}"
    deny '/home/' "${src}" "残留本机绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "station-ops 门禁失败 ${FAILS} 项"
say "station-ops 门禁通过"
