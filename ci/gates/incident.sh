#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/incident.sh — 事件响应（P-14）的布局、状态机与复盘门禁
#
# 用法:
#   ci/gates/incident.sh
#
# 断言链：
#   1. 台账包、入口与运行手册齐全，源码带 SPDX 头。
#   2. 全部模块可编译，单元测试覆盖模型、台账、命令行与复盘。
#   3. 失败路径退出码可归因：参数错 2、台账错 3、状态机拒绝 4。
#   4. 一条事件可从检测走到复盘，报告落到台账目录。
#   5. 告警取样按编号幂等，重复执行不产生重复事件。
#   6. CMake 安装接线成立，包内不得残留桩标记。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SYS="${ROOT}/system"
PKG="${SYS}/ops/incident"
ENTRY="${SYS}/bin/airy-incident"
AT="2026-01-02T03:04:05Z"
ID="INC-20260102-001"

need_cmd python3

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
LEDGER="${WORK}/ledger"

inc() {
    python3 "${ENTRY}" --root "${LEDGER}" "$@"
}

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "incident: 布局"
for name in __init__.py __main__.py model.py store.py feed.py runbook.py \
    cli.py; do
    [ -f "${PKG}/${name}" ] || bad "缺少模块 ${name}"
done
for book in crash-loop security-violation memory-pressure storage-exhausted; do
    [ -f "${PKG}/runbooks/${book}.md" ] || bad "缺少运行手册 ${book}"
done
[ -x "${ENTRY}" ] || bad "入口不可执行: bin/airy-incident"
for src in "${PKG}"/*.py "${ENTRY}"; do
    have '^# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0' \
        "${src}" "缺少 SPDX 标识: ${src}"
done
endgroup

# ─── 2. 编译与单元测试 ───────────────────────────────────────────────
group "incident: 编译与单元测试"
if ! python3 -m py_compile "${PKG}"/*.py; then
    bad "事件台账包无法编译"
fi
if ! (cd "${SYS}" && python3 -m unittest discover \
    -s tests/ops -p 'test_incident_*.py'); then
    bad "事件台账单元测试未通过"
fi
endgroup

# ─── 3. 失败路径退出码 ───────────────────────────────────────────────
group "incident: 失败路径"
rc=0
inc create --severity P9 --category system --title x >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 2 ] || bad "坏级别退出码 ${rc}，期望 2"

rc=0
inc show INC-20260102-009 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "缺事件退出码 ${rc}，期望 3"

inc create --severity P1 --category system --title t --at "${AT}" \
    >/dev/null 2>&1 || bad "登记事件失败"
rc=0
inc resolve "${ID}" --at "${AT}" >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "越级迁移退出码 ${rc}，期望 4"
endgroup

# ─── 4. 从检测到复盘 ─────────────────────────────────────────────────
group "incident: 闭环与复盘"
for step in triage respond recover; do
    inc "${step}" "${ID}" --at "${AT}" >/dev/null || bad "${step} 失败"
done
inc resolve "${ID}" --at "${AT}" --root-cause "配置项缺失" \
    >/dev/null || bad "resolve 失败"
inc postmortem "${ID}" --at "${AT}" --summary "已知悉" \
    >/dev/null || bad "postmortem 失败"
REPORT="${LEDGER}/postmortem/PM-20260102-001.md"
[ -f "${REPORT}" ] || bad "复盘报告未落盘"
have '### 4\.2 根本原因' "${REPORT}" "复盘缺少根因段"
have '配置项缺失' "${REPORT}" "复盘未记录根因结论"
have '"status":"POSTMORTEM"' <(inc show "${ID}" --json) \
    "状态未进入 POSTMORTEM"
endgroup

# ─── 5. 告警取样幂等 ─────────────────────────────────────────────────
group "incident: 告警取样"
mkdir -p "${WORK}/alerts"
cat >"${WORK}/alerts/active.json" <<'JSON'
{"schema":1,"alerts":[
 {"alert_id":"ALT-1","severity":"CRITICAL","source":"sec_d","code":7,
  "message":"拒绝越权写入","agent":5,"state":"FIRING"},
 {"alert_id":"ALT-2","severity":"INFO","source":"logger_d","code":1,
  "message":"例行","agent":0,"state":"FIRING"}]}
JSON
first="$(inc ingest --alerts-root "${WORK}/alerts" --at "${AT}" --json)" \
    || bad "首次取样失败"
have '"created":\["INC-20260102-002"\]' <(printf '%s\n' "${first}") \
    "首次取样未按告警建单"
second="$(inc ingest --alerts-root "${WORK}/alerts" --at "${AT}" --json)" \
    || bad "再次取样失败"
have '"created":\[\]' <(printf '%s\n' "${second}") \
    "重复取样产生了重复事件"
endgroup

# ─── 6. 安装接线与桩标记 ─────────────────────────────────────────────
group "incident: 安装与桩标记"
have 'ops/incident' "${SYS}/CMakeLists.txt" "CMake 未安装台账包"
have 'bin/airy-incident' "${SYS}/CMakeLists.txt" "CMake 未安装命令行入口"
have 'share/airymax/incident/runbooks' "${SYS}/CMakeLists.txt" \
    "CMake 未安装运行手册"
for src in "${PKG}"/*.py; do
    deny 'NotImplementedError|TODO|FIXME|breakpoint\(' "${src}" \
        "残留桩标记: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "incident 门禁失败 ${FAILS} 项"
say "incident 门禁通过"
