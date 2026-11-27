#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_lang_lock.sh — ci/gates/lang-lock.sh 的语言政策回归
#
# 钉住判定逻辑（distro/lockfile/lang_check.py）真能判负：policy 不变量被
# 改、planned 组件提前出现 Cargo.toml、present 组件缺锁文件，三种坏输入
# 都必须退 1 并报出对应条目；合规树退 0。夹具用临时源根，不改真树。
#
# 用法: bash ci/tests/test_lang_lock.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/lang-lock.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# mkfix —— 按政策造一棵最小合规树：4 个 present 组件共用 system/tui 工作区，
# 另有 kernel/distro-presets 上游路径与 legacy C 文件。
mkfix()
{
    local c

    rm -rf "${FX}"
    mkdir -p "${FX}/ci/container" "${FX}/distro/lockfile" \
        "${FX}/distro/presets" "${FX}/kernel" "${FX}/system/bin" \
        "${FX}/system/tui/crates"
    cp "${ROOT}/distro/lockfile/lang_check.py" \
        "${ROOT}/distro/lockfile/lang-policy.yaml" "${FX}/distro/lockfile/"
    printf 'rust\ncargo\n' >"${FX}/ci/container/build-packages.txt"
    for c in boot-tui boot-ime asr-gateway intent; do
        mkdir -p "${FX}/system/tui/crates/${c}"
        printf '[package]\nname = "%s"\n' "${c}" \
            >"${FX}/system/tui/crates/${c}/Cargo.toml"
    done
    printf '[workspace]\nmembers = []\n' >"${FX}/system/tui/Cargo.toml"
    : >"${FX}/system/tui/Cargo.lock"
    printf '[toolchain]\nchannel = "1.90.0"\n' \
        >"${FX}/system/tui/rust-toolchain.toml"
    : >"${FX}/system/bin/airy-rt-lat.c"
}

run()
{
    bash "${GATE}" --source "${FX}" 2>&1
}

check()
{
    local name="$1" want="$2" rc="$3" out="$4" needle="$5"

    if [ "${rc}" != "${want}" ]; then
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

mkfix
out="$(run)"; rc=$?
check "合规树判通过" 0 "${rc}" "${out}" "语言政策同源通过"

mkfix
sed -i 's/^  default: rust$/  default: c/' "${FX}/distro/lockfile/lang-policy.yaml"
out="$(run)"; rc=$?
check "默认语言被改判负" 1 "${rc}" "${out}" "policy.default 必须为 rust"

mkfix
mkdir -p "${FX}/desktop/dock"
: >"${FX}/desktop/dock/Cargo.toml"
out="$(run)"; rc=$?
check "planned 提前落地判负" 1 "${rc}" "${out}" "已出现 Cargo.toml"

mkfix
rm -f "${FX}/system/tui/Cargo.lock"
out="$(run)"; rc=$?
check "present 缺锁文件判负" 1 "${rc}" "${out}" "缺少 Cargo.lock"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
