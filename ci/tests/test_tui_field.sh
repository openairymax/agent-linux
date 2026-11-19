#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_tui_field.sh — 取证门禁与采集器的参数契约回归
#
# 门禁本体要调 cargo 拉真实校验器，耗时以十秒计；参数契约与采集器的落盘
# 格式是秒级可判的。这里把门禁、采集器与它们依赖的 lib 复制到临时根，
# 断言三类失败分支的退出码，并用夹具模式跑一遍采集器，检查记录确实
# 带 fixture 标记、用例前缀与截图摘要；实机模式的拒绝路径也在开发机上
# 可判——开发机不是 AirymaxOS，拒绝正是校验在起作用。
#
# 用法: bash ci/tests/test_tui_field.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/tui-field.sh"
TOOL="${FX}/ci/tools/tui-field-collect.sh"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/ci/tools" "${FX}/system/tui"
cp "${ROOT}/ci/gates/tui-field.sh" "${GATE}"
cp "${ROOT}/ci/tools/tui-field-collect.sh" "${TOOL}"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/tui-gate.sh" "${FX}/ci/lib/tui-gate.sh"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 门禁与采集器都会读 CI 专属环境文件；继承它们会让诊断输出静默改道。
run()
{
    env -u GITHUB_OUTPUT -u GITHUB_ENV -u GITHUB_ACTIONS \
        bash "$@" 2>&1
}

expect()
{
    local name="$1" want="$2" got="$3" out="$4"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    ok "${name}"
}

out="$(run "${GATE}" --help)"
expect "门禁 --help 退出 0" 0 "$?" "${out}"

out="$(run "${GATE}" --bogus)"
expect "门禁未知参数退出 5" 5 "$?" "${out}"

out="$(run "${GATE}" --input "${tmp}/absent")"
expect "门禁 --input 不存在退出 5" 5 "$?" "${out}"

out="$(run "${GATE}" --root "${tmp}/absent")"
expect "门禁 --root 不存在退出 5" 5 "$?" "${out}"

out="$(run "${GATE}" --root "${FX}")"
expect "门禁缺 Cargo.lock 退出 1" 1 "$?" "${out}"

out="$(run "${TOOL}" --help)"
expect "采集器 --help 退出 0" 0 "$?" "${out}"

out="$(run "${TOOL}" --bogus)"
expect "采集器未知参数退出 5" 5 "$?" "${out}"

out="$(run "${TOOL}" --case ime-recover --observed x)"
expect "采集器缺 --out 退出 5" 5 "$?" "${out}"

out="$(run "${TOOL}" --out "${tmp}/out" --case nope --observed x --fixture)"
expect "采集器未知用例退出 5" 5 "$?" "${out}"

out="$(run "${TOOL}" --out "${tmp}/out" --case ime-recover --observed x --fixture)"
expect "采集器夹具缺尺寸退出 5" 5 "$?" "${out}"

shot="${tmp}/shot.png"
printf 'fixture' >"${shot}"
out="$(run "${TOOL}" --out "${tmp}/out" --case ime-recover --observed "ime=ok" \
    --fixture --arch x86_64 --cols 85 --rows 24 --screenshot "${shot}")"
expect "采集器夹具写入退出 0" 0 "$?" "${out}"

REC="${tmp}/out/x86_64-fixture-ime-recover.field"
if [ -f "${REC}" ]; then
    ok "记录落盘"
    head -n 1 "${REC}" | grep -qx 'AIRYMAX-TUI-FIELD/1' && ok "起始标记" || bad "起始标记"
    grep -qx 'evidence=fixture' "${REC}" && ok "夹具标记" || bad "夹具标记"
    grep -qx 'case=fixture-ime-recover' "${REC}" && ok "用例前缀" || bad "用例前缀"
    grep -Eq '^screenshot=[0-9a-f]{64}$' "${REC}" && ok "截图摘要" || bad "截图摘要"
    tail -n 1 "${REC}" | grep -qx 'AIRYMAX-TUI-FIELD-END' && ok "结束标记" || bad "结束标记"
else
    bad "记录未落盘: ${REC}"
fi

out="$(run "${TOOL}" --out "${tmp}/out" --case ime-recover --observed x \
    --fixture --arch x86_64 --cols 85 --rows 24)"
expect "同格覆盖退出 1" 1 "$?" "${out}"

out="$(run "${TOOL}" --out "${tmp}/out2" --case ime-recover --observed x)"
expect "开发机实机模式拒绝退出 1" 1 "$?" "${out}"

if [ "${fails}" -ne 0 ]; then
    printf 'test_tui_field: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_tui_field: 全部通过\n'
