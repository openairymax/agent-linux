#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_alert_d.sh — ci/gates/alert-d.sh 的判据回归
#
# 钉住布局与接线判据：默认规则缺件、模块缺 SPDX、守护入口不可执行、CMake
# 未装守护入口各判负；删掉 airy-alert.service 后 units_lint 的单元契约判负。
# 正向用原树跑完编译、出厂规则 CLI、单元测试与三条失败路径退出码。
#
# 用法: bash ci/tests/test_alert_d.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/alert-d.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/services/daemons" "${FX}/system/services" \
        "${FX}/system/tools" "${FX}/system/tests/services" \
        "${FX}/distro/specs"
    cp "${ROOT}/ci/gates/alert-d.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -R "${ROOT}/system/services/daemons/alert_d" \
        "${FX}/system/services/daemons/"
    cp -R "${ROOT}/system/services/airylib" "${FX}/system/services/"
    cp "${ROOT}/system/bin/airy-alertd" "${ROOT}/system/bin/airy-alert" \
        "${FX}/system/bin/"
    chmod 0755 "${FX}/system/bin/airy-alertd" "${FX}/system/bin/airy-alert"
    cp -R "${ROOT}/system/units" "${ROOT}/system/scripts" "${FX}/system/"
    cp "${ROOT}/system/tools/units_lint.py" "${FX}/system/tools/"
    cp "${ROOT}/system/CMakeLists.txt" "${FX}/system/"
    cp "${ROOT}"/system/tests/services/test_alert_*.py \
        "${FX}/system/tests/services/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
}

run()
{
    out="$(bash "${GATE}" "$@" 2>&1)"
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
check "原树布局与行为放行" 0 "alert-d 门禁通过"

setup
rm "${FX}/system/services/daemons/alert_d/rules/10-default.json"
run
check "默认规则缺件判负" 1 "缺少默认规则"

setup
printf '\n# TODO: 待补规则\n' \
    >>"${FX}/system/services/daemons/alert_d/rules.py"
run
check "桩标记判负" 1 "残留桩标记"

setup
sed -i '1d' "${FX}/system/services/daemons/alert_d/model.py"
run
check "首行缺 SPDX 判负" 1 "缺少 SPDX 标识"

setup
chmod -x "${FX}/system/bin/airy-alertd"
run
check "守护入口不可执行判负" 1 "入口不可执行: airy-alertd"

setup
sed -i 's|bin/airy-alertd|bin/airy-alertx|' "${FX}/system/CMakeLists.txt"
run
check "CMake 未装守护判负" 1 "CMake 未安装守护入口"

setup
rm "${FX}/system/units/airy-alert.service"
run
check "单元契约缺件判负" 1 "systemd 单元契约未通过"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
