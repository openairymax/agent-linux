#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_model_manifest.sh — model-manifest 门禁回归夹具
#
# 钉住的判据：
#   负向：入口不可执行/首行非 python3、单元缺契约行、preset 未钉 disabled、
#         tmpfiles 未交运行根、配方漏交付入口或账户组、源码树出现 *.gguf，
#         均判 1；未知参数判 1。
#   正向：真实入口与声明自洽时判 0，实物面 install/status/verify 按 0/10/1
#         与 degraded:no-accel、failed:digest-mismatch/no-manifest 收敛。
#
# 用法: bash ci/tests/test_model_manifest.sh
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
        "${FX}/system/units" "${FX}/system/services" "${FX}/distro/presets" \
        "${FX}/distro/specs"
    cp "${ROOT}/ci/gates/model-manifest.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/check.sh" \
        "${ROOT}/ci/lib/model_assets.sh" "${FX}/ci/lib/"
    cp -p "${ROOT}/system/bin/airy-model" "${FX}/system/bin/"
    cp "${ROOT}/system/units/airy-llm.service" \
        "${ROOT}/system/units/airymax-llm.conf" "${FX}/system/units/"
    cp -a "${ROOT}/system/services/airyllm" \
        "${ROOT}/system/services/airymax_rt" "${FX}/system/services/"
    cp "${ROOT}/distro/presets/90-airymax.preset" "${FX}/distro/presets/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
}

run() { bash "${FX}/ci/gates/model-manifest.sh" "$@" 2>&1; }

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

# 正向：声明面 + 实物面全绿
seed
out="$(run)"; rc=$?
check "真实树判 0" 0 "${rc}" "${out}" "== model-manifest passed =="
printf '%s' "${out}" | grep -qF 'install/status/verify 按 0/10 契约收敛' &&
    ok "实物面契约收敛" || bad "实物面未跑"
printf '%s' "${out}" | grep -qF '篡改被识别为 digest-mismatch' &&
    ok "篡改判 1 被识别" || bad "篡改样本未跑"

# 负向：入口丢失
seed
rm -f "${FX}/system/bin/airy-model"
out="$(run)"; rc=$?
check "入口丢失判负" 1 "${rc}" "${out}" "入口不可执行"

# 负向：入口首行不是 python3 shebang
seed
sed -i '1s#.*#\#!/bin/sh#' "${FX}/system/bin/airy-model"
out="$(run)"; rc=$?
check "入口 shebang 判负" 1 "${rc}" "${out}" "入口首行不是 python3 shebang"

# 负向：单元缺契约行
seed
sed -i '/^User=airy$/d' "${FX}/system/units/airy-llm.service"
out="$(run)"; rc=$?
check "单元缺契约行判负" 1 "${rc}" "${out}" "单元缺少契约行: User=airy"

# 负向：preset 未钉 disabled
seed
sed -i '/^disable airy-llm.service$/d' "${FX}/distro/presets/90-airymax.preset"
out="$(run)"; rc=$?
check "preset 未钉判负" 1 "${rc}" "${out}" "preset 未把 airy-llm.service 钉为 disabled"

# 负向：tmpfiles 未交运行根
seed
sed -i '/^d \/var\/lib\/airy-llm 0755 airy airy -$/d' \
    "${FX}/system/units/airymax-llm.conf"
out="$(run)"; rc=$?
check "tmpfiles 缺声明判负" 1 "${rc}" "${out}" "tmpfiles 条目未把运行根交给服务账户"

# 负向：配方未交付入口
seed
sed -i '/%{_bindir}\/airy-model/d' "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "配方未交付入口判负" 1 "${rc}" "${out}" "配方未交付: %{_bindir}/airy-model"

# 负向：配方组清单缺 airy-cogn
seed
sed -i 's/^for grp in airy airy-cogn /for grp in airy /' \
    "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "组清单缺项判负" 1 "${rc}" "${out}" "服务组清单缺少 airy-cogn"

# 负向：账户块丢补充组
seed
sed -i 's/ -G airy-cogn / /' "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "账户缺补充组判负" 1 "${rc}" "${out}" "airy 账户缺少 -G airy-cogn"

# 负向：源码树出现模型权重
seed
printf 'fake\n' >"${FX}/system/leaked.gguf"
out="$(run)"; rc=$?
check "权重入库判负" 1 "${rc}" "${out}" "源码树内出现模型权重"

# 负向：缺单元 / 缺配方；参数面
seed
rm -f "${FX}/system/units/airy-llm.service"
out="$(run)"; rc=$?
check "缺单元判负" 1 "${rc}" "${out}" "缺少单元文件"
seed
rm -f "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "缺配方判负" 1 "${rc}" "${out}" "缺少配方"
seed
out="$(run --bogus)"; rc=$?
check "未知参数判 1" 1 "${rc}" "${out}" "未知参数: --bogus"
out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
