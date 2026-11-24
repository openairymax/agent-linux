#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/alert-d.sh — 告警聚合（P-13）的布局、契约与行为门禁
#
# 用法:
#   ci/gates/alert-d.sh
#
# 断言链：
#   1. 包体、入口、单元与默认规则齐全，源码带 SPDX 头。
#   2. 全部模块可编译，出厂规则可通过 CLI 校验。
#   3. 单元测试覆盖模型、规则、引擎、持久化与守护端到端。
#   4. 失败路径退出码可归因：坏规则 3、坏限额 2、无状态 3。
#   5. systemd 单元契约与 CMake 安装接线成立。
#   6. 包内不得残留桩标记。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SYS="${ROOT}/system"
PKG="${SYS}/services/daemons/alert_d"
UNIT="${SYS}/units/airy-alert.service"

need_cmd python3

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "alert-d: 布局"
for name in __init__.py __main__.py model.py rules.py engine.py \
    store.py server.py cli.py; do
    [ -f "${PKG}/${name}" ] || bad "缺少模块 ${name}"
done
[ -f "${PKG}/rules/10-default.json" ] || bad "缺少默认规则"
[ -f "${UNIT}" ] || bad "缺少 ${UNIT}"
for entry in airy-alertd airy-alert; do
    [ -x "${SYS}/bin/${entry}" ] || bad "入口不可执行: ${entry}"
done
for src in "${PKG}"/*.py "${SYS}"/bin/airy-alert*; do
    have '^# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0' \
        "${src}" "缺少 SPDX 标识: ${src}"
done
endgroup

# ─── 2. 编译与出厂规则 ───────────────────────────────────────────────
group "alert-d: 编译与规则"
if ! python3 -m py_compile "${PKG}"/*.py; then
    bad "告警包无法编译"
fi
if ! python3 "${SYS}/bin/airy-alert" rules --rules "${PKG}/rules" \
    >"${WORK}/rules.out" 2>&1; then
    bad "出厂规则未通过校验"
fi
endgroup

# ─── 3. 单元测试 ─────────────────────────────────────────────────────
group "alert-d: 单元测试"
if ! (cd "${SYS}" && python3 -m unittest discover \
    -s tests/services -p 'test_alert_*.py'); then
    bad "告警单元测试未通过"
fi
endgroup

# ─── 4. 失败路径退出码 ───────────────────────────────────────────────
group "alert-d: 失败路径"
rc=0
python3 "${SYS}/bin/airy-alertd" --socket "${WORK}/a.sock" \
    --rules "${WORK}/absent" --state "${WORK}/state" --once \
    >"${WORK}/daemon.out" 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "坏规则目录退出码 ${rc}，期望 3"

rc=0
python3 "${SYS}/bin/airy-alert" history --state "${WORK}/state" \
    --limit 0 >"${WORK}/limit.out" 2>&1 || rc=$?
[ "${rc}" -eq 2 ] || bad "坏限额退出码 ${rc}，期望 2"

rc=0
python3 "${SYS}/bin/airy-alert" status --state "${WORK}/state" \
    >"${WORK}/status.out" 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "缺状态退出码 ${rc}，期望 3"
endgroup

# ─── 5. 单元契约与安装接线 ───────────────────────────────────────────
group "alert-d: 单元与接线"
if ! python3 "${SYS}/tools/units_lint.py" "${SYS}/units" >/dev/null; then
    bad "systemd 单元契约未通过"
fi
have 'airy-alert\.service' "${SYS}/tools/units_lint.py" \
    "units_lint 未登记 airy-alert.service"
have 'services/daemons/alert_d' "${SYS}/CMakeLists.txt" \
    "CMake 未安装告警包"
have 'bin/airy-alertd' "${SYS}/CMakeLists.txt" "CMake 未安装守护入口"
endgroup

# ─── 6. 桩标记扫描 ───────────────────────────────────────────────────
group "alert-d: 桩标记"
for src in "${PKG}"/*.py; do
    deny 'NotImplementedError|TODO|FIXME|breakpoint\(' "${src}" \
        "残留桩标记: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "alert-d 门禁失败 ${FAILS} 项"
say "alert-d 门禁通过"
