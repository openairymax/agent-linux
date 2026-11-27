#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_backlight.sh — ci/gates/backlight.sh 的判据回归
#
# 钉住声明面五条：包内模块缺一即判负；TODO/NotImplementedError 桩标记判负；
# 代码行硬编码机型设备名判负（注释里的取证记录不算）；spec 未声明源包或未
# 安装入口判负；入口不可执行判负。实物面由原树跑通（临时 sysfs 树、无特权）。
#
# 用法: bash ci/tests/test_backlight.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/backlight.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/services" "${FX}/distro/specs"
    cp "${ROOT}/ci/gates/backlight.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/check.sh" "${FX}/ci/lib/"
    cp "${ROOT}/system/bin/airy-bright" "${FX}/system/bin/"
    chmod 0755 "${FX}/system/bin/airy-bright"
    cp -R "${ROOT}/system/services/airybright" "${FX}/system/services/"
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
run --tree "${FX}"
check "原树声明面与实物面放行" 0 "背光入口通过"

setup
rm "${FX}/system/services/airybright/config.py"
run --tree "${FX}"
check "缺模块判负" 1 "package: 缺少 config.py"

setup
printf '\n# TODO: 待补机型\n' >>"${FX}/system/services/airybright/cli.py"
run --tree "${FX}"
check "桩标记判负" 1 "package: 存在桩标记"

setup
printf '\nDEVICE_HINT = "intel_backlight"\n' \
    >>"${FX}/system/services/airybright/config.py"
run --tree "${FX}"
check "硬编码机型判负" 1 "package: 代码里硬编码了机型设备名"

setup
sed -i '/system\/services\/airybright/d' \
    "${FX}/distro/specs/airymaxos-system.spec"
run --tree "${FX}"
check "spec 未声明源包判负" 1 "spec: 未声明 services/airybright 源包"

setup
sed -i '/%{_bindir}\/airy-bright/d' "${FX}/distro/specs/airymaxos-system.spec"
run --tree "${FX}"
check "spec 未安装入口判负" 1 "spec: 未安装 airy-bright"

setup
chmod -x "${FX}/system/bin/airy-bright"
run --tree "${FX}"
check "入口不可执行判负" 1 "entry: system/bin/airy-bright 不可执行"

setup
run --tree "${FX}" --bogus
check "未知参数退 5" 5 "未知参数"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
