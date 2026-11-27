#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_memory_ops.sh — memory-ops 门禁回归夹具
#
# 钉住的判据：
#   负向：缺模块/测试、入口不可执行、源文件缺 SPDX、残留 TODO 或 /home/ 路径、
#         spec 漏登记入口/运维包、workflow 漏作业、CMake 漏安装入口，均判 1。
#   正向：真实运维包与接线自洽时判 0，数据面与命令行按 format/index、defrag、
#         sensitive、crypto=3、配置 3 等契约收敛。
#
# 用法: bash ci/tests/test_memory_ops.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

seed()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/ops" "${FX}/system/tests/ops" "${FX}/system/services" \
        "${FX}/distro/specs" "${FX}/.github/workflows"
    cp "${ROOT}/ci/gates/memory-ops.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -a "${ROOT}/system/ops/memory" "${FX}/system/ops/"
    cp -p "${ROOT}/system/bin/airy-memory" "${FX}/system/bin/"
    cp "${ROOT}"/system/tests/ops/test_memory_*.py "${FX}/system/tests/ops/"
    cp -a "${ROOT}/system/services/airylib" "${FX}/system/services/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
    cp "${ROOT}/.github/workflows/agent-verify.yml" "${FX}/.github/workflows/"
    cp "${ROOT}/system/CMakeLists.txt" "${FX}/system/"
}

run() { bash "${FX}/ci/gates/memory-ops.sh" 2>&1; }

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"
    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${needle}" ] && ! printf '%s' "${out}" | grep -qF -- "${needle}"; then
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

# 正向：布局、测试、数据面、命令行、接线全绿
seed
out="$(run)"; rc=$?
check "真实树判 0" 0 "${rc}" "${out}" "memory-ops 门禁通过"
printf '%s' "${out}" | grep -qF 'format=ok index=ok' &&
    ok "块格式与索引一致" || bad "数据面未跑"
printf '%s' "${out}" | grep -qF 'crypto=3' &&
    ok "crypto 显式拒绝为 3" || bad "crypto 样本未跑"
printf '%s' "${out}" | grep -qF 'sensitive=rejected' &&
    ok "敏感项迁移被拒" || bad "敏感项样本未跑"

# 负向：缺模块
seed
rm -f "${FX}/system/ops/memory/stats.py"
out="$(run)"; rc=$?
check "缺模块判负" 1 "${rc}" "${out}" "缺少模块 stats.py"

# 负向：缺测试
seed
rm -f "${FX}/system/tests/ops/test_memory_stats.py"
out="$(run)"; rc=$?
check "缺测试判负" 1 "${rc}" "${out}" "缺少测试 test_memory_stats.py"

# 负向：入口不可执行
seed
chmod -x "${FX}/system/bin/airy-memory"
out="$(run)"; rc=$?
check "入口不可执行判负" 1 "${rc}" "${out}" "入口不可执行: bin/airy-memory"

# 负向：源文件首行缺 SPDX
seed
sed -i '1d' "${FX}/system/ops/memory/model.py"
out="$(run)"; rc=$?
check "缺 SPDX 判负" 1 "${rc}" "${out}" "缺少 SPDX 标识"

# 负向：残留桩标记
seed
printf '\n# TODO: fill me\n' >>"${FX}/system/ops/memory/config.py"
out="$(run)"; rc=$?
check "残留桩标记判负" 1 "${rc}" "${out}" "残留桩标记"

# 负向：残留本机绝对路径
seed
printf '\n# /home/dev/notes\n' >>"${FX}/system/ops/memory/parser.py"
out="$(run)"; rc=$?
check "本机路径判负" 1 "${rc}" "${out}" "残留本机绝对路径"

# 负向：spec 漏登记入口源
seed
sed -i '/^Source103:/d' "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "spec 漏入口源判负" 1 "${rc}" "${out}" "spec 未登记入口源"

# 负向：spec 漏打包运维模块
seed
sed -i '/^%{python3_sitelib}\/memory$/d' \
    "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "spec 漏打包判负" 1 "${rc}" "${out}" "spec 未打包运维模块"

# 负向：workflow 漏 memory 作业
seed
sed -i '/^  memory-verify:$/d' "${FX}/.github/workflows/agent-verify.yml"
out="$(run)"; rc=$?
check "workflow 漏作业判负" 1 "${rc}" "${out}" "workflow 未新增 memory 作业"

# 负向：CMake 漏安装入口
seed
sed -i '/bin\/airy-memory/d' "${FX}/system/CMakeLists.txt"
out="$(run)"; rc=$?
check "CMake 漏入口判负" 1 "${rc}" "${out}" "CMake 未安装入口"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
