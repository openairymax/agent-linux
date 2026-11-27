#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_agent_ops.sh — ci/gates/agent-ops.sh 的判据回归
#
# 钉住布局与接线两类判据：模块/测试缺件、入口不可执行、首行缺 SPDX、包内
# 桩标记与本机绝对路径各判负一次；CMake 未装入口、spec 未打包运维模块、
# workflow 未新增 agent 作业也各判负。正向用原树跑完状态机、健康分与权限断言。
#
# 用法: bash ci/tests/test_agent_ops.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/agent-ops.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/ops" "${FX}/system/services" "${FX}/system/tests/ops" \
        "${FX}/distro/specs" "${FX}/.github/workflows"
    cp "${ROOT}/ci/gates/agent-ops.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -R "${ROOT}/system/ops/agent" "${FX}/system/ops/"
    cp -R "${ROOT}/system/services/airylib" "${FX}/system/services/"
    cp "${ROOT}/system/bin/airy-agent" "${FX}/system/bin/"
    chmod 0755 "${FX}/system/bin/airy-agent"
    cp "${ROOT}"/system/tests/ops/test_agent_*.py "${FX}/system/tests/ops/"
    cp "${ROOT}/system/CMakeLists.txt" "${FX}/system/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
    cp "${ROOT}/.github/workflows/agent-verify.yml" "${FX}/.github/workflows/"
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
check "原树布局与状态机放行" 0 "agent-ops 门禁通过"

setup
rm "${FX}/system/tests/ops/test_agent_cli.py"
run
check "缺测试判负" 1 "缺少测试 test_agent_cli.py"

setup
printf '\n# TODO: 待补\n' >>"${FX}/system/ops/agent/model.py"
run
check "桩标记判负" 1 "残留桩标记"

setup
printf '\n# 调试用 /home/dev/agent\n' >>"${FX}/system/ops/agent/logs.py"
run
check "本机绝对路径判负" 1 "残留本机绝对路径"

setup
sed -i '1d' "${FX}/system/ops/agent/cli.py"
run
check "首行缺 SPDX 判负" 1 "缺少 SPDX 标识"

setup
chmod -x "${FX}/system/bin/airy-agent"
run
check "入口不可执行判负" 1 "入口不可执行"

setup
sed -i 's|bin/airy-agent|bin/airy-agenx|' "${FX}/system/CMakeLists.txt"
run
check "CMake 未装入口判负" 1 "CMake 未安装入口"

setup
sed -i '/%{python3_sitelib}\/agent$/d' \
    "${FX}/distro/specs/airymaxos-system.spec"
run
check "spec 未打包模块判负" 1 "spec 未打包运维模块"

setup
sed -i 's/^  agent-verify:/  agent-check:/' \
    "${FX}/.github/workflows/agent-verify.yml"
run
check "workflow 缺作业判负" 1 "workflow 未新增 agent 作业"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
