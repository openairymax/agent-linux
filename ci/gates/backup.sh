#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/backup.sh — 备份恢复（P-15）的布局、核验、恢复与保留期门禁
#
# 用法:
#   ci/gates/backup.sh
#
# 断言链：
#   1. 备份包、入口与定时单元齐全，源码带 SPDX 头。
#   2. 全部模块可编译，单元测试覆盖模型、归档、仓库与命令行。
#   3. 失败路径退出码可归因：参数错 2、仓库缺件 3、完整性失败 4。
#   4. 一次全量备份可核验、可恢复、可单 Agent 回放，摘要与索引落盘。
#   5. 篡改后的归档在解包前被拒，目标目录不产生半份数据。
#   6. 保留期清理删旧留新，最新全量在任何情况下都保留。
#   7. 定时单元与 CMake 安装接线成立，包内不得残留桩标记。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SYS="${ROOT}/system"
PKG="${SYS}/ops/backup"
ENTRY="${SYS}/bin/airy-backup"

need_cmd python3

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
REPO="${WORK}/repo"
SRC="${WORK}/src"
AT="2026-07-18T02:00:00Z"
LATER="2026-09-01T02:00:00Z"

mkdir -p "${SRC}/var/lib/airy/agents/7" "${SRC}/var/lib/airy/agents/8" \
    "${SRC}/var/lib/airy/memory/l3/7"
printf '{"agent": 7}\n' >"${SRC}/var/lib/airy/agents/7/state.json"
printf '{"agent": 8}\n' >"${SRC}/var/lib/airy/agents/8/state.json"
printf 'memory\n' >"${SRC}/var/lib/airy/memory/l3/7/m.txt"

bk() {
    python3 "${ENTRY}" --root "${REPO}" "$@"
}

json_field() {
    python3 -c 'import json,sys;print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "backup: 布局"
for name in __init__.py __main__.py model.py archive.py store.py cli.py; do
    [ -f "${PKG}/${name}" ] || bad "缺少模块 ${name}"
done
[ -x "${ENTRY}" ] || bad "入口不可执行: bin/airy-backup"
for src in "${PKG}"/*.py "${ENTRY}"; do
    have '^# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0' \
        "${src}" "缺少 SPDX 标识: ${src}"
done
for unit in airymax-backup.service airymax-backup.timer; do
    [ -f "${SYS}/units/${unit}" ] || bad "缺少单元 ${unit}"
done
have '^OnCalendar=hourly$' "${SYS}/units/airymax-backup.timer" \
    "备份定时器不是按小时触发"
have '^ExecStart=/usr/bin/airy-backup --actor systemd auto$' \
    "${SYS}/units/airymax-backup.service" "备份单元未调用 auto"
have '^StateDirectory=airy/backup$' "${SYS}/units/airymax-backup.service" \
    "备份单元未声明状态目录"
endgroup

# ─── 2. 编译与单元测试 ───────────────────────────────────────────────
group "backup: 编译与单元测试"
if ! python3 -m py_compile "${PKG}"/*.py; then
    bad "备份包无法编译"
fi
if ! (cd "${SYS}" && python3 -m unittest discover \
    -s tests/ops -p 'test_backup_*.py'); then
    bad "备份单元测试未通过"
fi
endgroup

# ─── 3. 失败路径退出码 ───────────────────────────────────────────────
group "backup: 失败路径"
rc=0
bk create --kind full --source bogus >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 2 ] || bad "坏来源退出码 ${rc}，期望 2"

rc=0
bk show FULL-20260718-999 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "缺备份退出码 ${rc}，期望 3"
endgroup

# ─── 4. 全量备份的核验与恢复 ─────────────────────────────────────────
group "backup: 核验与恢复"
created="$(bk --actor tester --at "${AT}" create --kind full \
    --source "var/lib/airy/agents=${SRC}/var/lib/airy/agents" \
    --source "var/lib/airy/memory/l3=${SRC}/var/lib/airy/memory/l3" \
    --json)" || bad "创建全量备份失败"
ID="$(printf '%s\n' "${created}" | json_field backup_id)"
[ "${ID}" = "FULL-20260718-001" ] || bad "备份编号 ${ID}，期望 FULL-20260718-001"
FILE="$(printf '%s\n' "${created}" | json_field file)"
case "${FILE}" in
    *.tar.zst | *.tar.gz) ;;
    *) bad "归档名未带编码后缀: ${FILE}" ;;
esac
[ -f "${REPO}/full/${FILE}" ] || bad "归档未落盘"
[ -f "${REPO}/full/${FILE}.sha256" ] || bad "缺少摘要旁文件"
(cd "${REPO}/full" && sha256sum -c "${FILE}.sha256" >/dev/null) \
    || bad "摘要旁文件与归档不符"
[ -f "${REPO}/manifest.json" ] || bad "全局索引未生成"
have "\"backup_id\": \"${ID}\"" "${REPO}/manifest.json" "索引缺少本次备份"

bk verify "${ID}" --deep >/dev/null || bad "核验未通过"

rc=0
bk restore "${ID}" --target "${WORK}/out0" --log "${WORK}/rec.log" \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 2 ] || bad "全量恢复未确认时退出码 ${rc}，期望 2"
[ ! -e "${WORK}/out0" ] || bad "未确认的全量恢复仍创建了目标目录"

OUT="${WORK}/out"
bk restore "${ID}" --target "${OUT}" --confirm --actor tester \
    --log "${WORK}/rec.log" >/dev/null || bad "全量恢复失败"
have '^{"agent": 7}$' "${OUT}/var/lib/airy/agents/7/state.json" \
    "Agent 7 状态未恢复"
have '^memory$' "${OUT}/var/lib/airy/memory/l3/7/m.txt" "L3 记忆未恢复"
have '"event": "restore"' "${WORK}/rec.log" "恢复日志缺少事件行"

ONE="${WORK}/one"
bk restore "${ID}" --target "${ONE}" --confirm --agent-id 7 \
    --log "${WORK}/rec.log" >/dev/null || bad "单 Agent 恢复失败"
[ -d "${ONE}/var/lib/airy/agents/7" ] || bad "单 Agent 恢复缺少 7"
[ ! -e "${ONE}/var/lib/airy/agents/8" ] || bad "单 Agent 恢复混入 8"
endgroup

# ─── 5. 篡改必须停在解包之前 ─────────────────────────────────────────
group "backup: 完整性"
inc="$(bk --actor tester --at "${AT}" create --kind incremental \
    --source "var/lib/airy/agents=${SRC}/var/lib/airy/agents" --json)" \
    || bad "创建增量备份失败"
INC_ID="$(printf '%s\n' "${inc}" | json_field backup_id)"
INC_FILE="$(printf '%s\n' "${inc}" | json_field file)"
printf 'tampered\n' >>"${REPO}/incremental/${INC_FILE}"

rc=0
bk verify "${INC_ID}" >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "篡改后核验退出码 ${rc}，期望 4"

rc=0
bk restore "${INC_ID}" --target "${WORK}/tampered" --log "${WORK}/rec.log" \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "篡改后恢复退出码 ${rc}，期望 4"
[ ! -e "${WORK}/tampered" ] || bad "篡改归档仍产生了恢复目标"
endgroup

# ─── 6. 保留期：删旧留新 ─────────────────────────────────────────────
group "backup: 保留期"
new="$(bk --actor tester --at "${LATER}" create --kind full \
    --source "var/lib/airy/agents=${SRC}/var/lib/airy/agents" --json)" \
    || bad "创建新全量备份失败"
NEW_ID="$(printf '%s\n' "${new}" | json_field backup_id)"
[ "${NEW_ID}" = "FULL-20260901-001" ] || bad "新全量编号 ${NEW_ID} 不符"

preview="$(bk --at "${LATER}" prune --json)" || bad "保留期预演失败"
have "\"${ID}\"" <(printf '%s\n' "${preview}") "预演未列入旧全量"
have "\"${INC_ID}\"" <(printf '%s\n' "${preview}") "预演未列入旧增量"
have '"removed": \[\]' <(printf '%s\n' "${preview}") "预演不应删除文件"

report="$(bk --at "${LATER}" prune --apply --json)" || bad "保留期清理失败"
have "\"${ID}\"" <(printf '%s\n' "${report}") "旧全量未被清理"
have "\"${INC_ID}\"" <(printf '%s\n' "${report}") "旧增量未被清理"
[ ! -e "${REPO}/full/${FILE}" ] || bad "旧全量归档仍存在"
bk verify "${NEW_ID}" --deep >/dev/null || bad "清理后新全量核验失败"
endgroup

# ─── 7. 安装接线与桩标记 ─────────────────────────────────────────────
group "backup: 安装与桩标记"
have 'ops/backup' "${SYS}/CMakeLists.txt" "CMake 未安装备份包"
have 'bin/airy-backup' "${SYS}/CMakeLists.txt" "CMake 未安装命令行入口"
have '^Description=.' "${SYS}/units/airymax-backup.timer" "定时器缺少自述"
for src in "${PKG}"/*.py; do
    deny 'NotImplementedError|TODO|FIXME|breakpoint\(' "${src}" \
        "残留桩标记: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "backup 门禁失败 ${FAILS} 项"
say "backup 门禁通过"
