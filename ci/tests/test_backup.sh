#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_backup.sh — ci/gates/backup.sh 的判据回归
#
# 钉住布局与接线判据：定时器不是每小时的 OnCalendar、单元未调用 auto、未声明
# 状态目录、包内 FIXME 桩标记、CMake 未装命令行入口、模块缺件、入口不可执行
# 各判负一次。正向用原树跑完创建、核验、恢复、篡改拒绝与保留期清理。
#
# 用法: bash ci/tests/test_backup.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/backup.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/ops" "${FX}/system/units" "${FX}/system/tests/ops"
    cp "${ROOT}/ci/gates/backup.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -R "${ROOT}/system/ops/backup" "${FX}/system/ops/"
    cp "${ROOT}/system/bin/airy-backup" "${FX}/system/bin/"
    chmod 0755 "${FX}/system/bin/airy-backup"
    cp "${ROOT}/system/units/airymax-backup.service" \
        "${ROOT}/system/units/airymax-backup.timer" "${FX}/system/units/"
    cp "${ROOT}"/system/tests/ops/test_backup_*.py "${FX}/system/tests/ops/"
    cp "${ROOT}/system/CMakeLists.txt" "${FX}/system/"
}

run()
{
    out="$(bash "${GATE}" 2>&1)"
    rc=$?
}

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

check()
{
    local name="$1" want="$2" needle="$3"

    if [ "${rc}" -ne "${want}" ]; then
        bad "${name}（rc=${rc}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *)
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        ;;
    esac
}

setup
run
check "原树创建核验恢复放行" 0 "backup 门禁通过"

setup
sed -i 's/^OnCalendar=hourly$/OnCalendar=daily/' \
    "${FX}/system/units/airymax-backup.timer"
run
check "定时器非每小时判负" 1 "备份定时器不是按小时触发"

setup
sed -i 's/ --actor systemd auto$/ --actor systemd manual/' \
    "${FX}/system/units/airymax-backup.service"
run
check "单元未调用 auto 判负" 1 "备份单元未调用 auto"

setup
sed -i 's|^StateDirectory=airy/backup$|StateDirectory=airy/bak|' \
    "${FX}/system/units/airymax-backup.service"
run
check "未声明状态目录判负" 1 "备份单元未声明状态目录"

setup
printf '\n# FIXME: 待补保留期边界\n' >>"${FX}/system/ops/backup/store.py"
run
check "桩标记判负" 1 "残留桩标记"

setup
sed -i 's|bin/airy-backup|bin/airybak|' "${FX}/system/CMakeLists.txt"
run
check "CMake 未装入库判负" 1 "CMake 未安装命令行入口"

setup
rm "${FX}/system/ops/backup/archive.py"
run
check "模块缺件判负" 1 "缺少模块 archive.py"

setup
chmod -x "${FX}/system/bin/airy-backup"
run
check "入口不可执行判负" 1 "入口不可执行: bin/airy-backup"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
