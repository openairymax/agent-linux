#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_toolchain_gate.sh — ci/gates/toolchain.sh 的分支回归
#
# 门禁两条路径都要真 docker 才能跑，回归用桩顶替：桩不碰缓存内容，只按
# 预置计划逐次给出成败，并把「第几次调用、什么模式」记进序列。断言序列
# 而不只断言措辞，才能同时钉住调用顺序：先离线试装 → 失败才进在线段 →
# 收尾再用离线自证。
#
# 用例来自 run 41613120035：在线段曾写成 `timeout 3600 dnf_run`，而
# timeout(1) 只认外部命令，三次重试全部退成
# 「timeout: failed to run command 'dnf_run'」，工具链永远补不齐。桩在
# PATH 里，函数没被真正执行时调用序列为空，断言立刻可见。
#
# 用法: bash ci/tests/test_toolchain_gate.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/ci/container" "${tmp}/stub"
cp "${ROOT}/ci/gates/toolchain.sh" "${FX}/ci/gates/toolchain.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/container/build-packages.txt" "${FX}/ci/container/build-packages.txt"

STUB_LOG="${tmp}/calls"
STUB_PLAN="${tmp}/plan"
export STUB_LOG STUB_PLAN

# 桩按计划逐次放行：计划第 N 行是第 N 次 docker 调用的成败。模式取自
# 环境变量 AIRYMAX_MODE；`dnf clean metadata` 那一类调用没有它。
cat >"${tmp}/stub/docker" <<'EOF'
#!/usr/bin/env bash
mode=""
for a in "$@"; do
    case "${a}" in
        AIRYMAX_MODE=*) mode="${a#AIRYMAX_MODE=}" ;;
    esac
done
[ -n "${mode}" ] || mode=clean
n=$(( $(wc -l <"${STUB_LOG}") + 1 ))
printf '%s\n' "${mode}" >>"${STUB_LOG}"
[ "$(sed -n "${n}p" "${STUB_PLAN}")" = ok ]
EOF
chmod +x "${tmp}/stub/docker"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# run PLAN —— 按 PLAN 跑门禁一次，输出到 stdout，返回门禁退出码。
run()
{
    printf '%s\n' "$1" >"${STUB_PLAN}"
    : >"${STUB_LOG}"
    PATH="${tmp}/stub:${PATH}" bash "${FX}/ci/gates/toolchain.sh" \
        --image test-image --cache "${tmp}/cache" 2>&1
}

# calls —— 把调用序列折成一行，便于整段比对。
calls()
{
    paste -sd' ' "${STUB_LOG}" 2>/dev/null
}

check()
{
    local name="$1" want_rc="$2" rc="$3" got="$4" want="$5" out="$6"

    if [ "${rc}" != "${want_rc}" ]; then
        bad "${name}（rc=${rc}，期望 ${want_rc}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ "${got}" != "${want}" ]; then
        bad "${name}（调用序列=「${got}」，期望「${want}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

out="$(run 'fail
fail
ok
ok
ok')"
rc=$?
check "冷缓存：在线第二次成功" 0 "${rc}" "$(calls)" \
    "offline online clean online offline" "${out}"

out="$(run 'fail
fail
ok
fail
ok
fail
ok')"
rc=$?
check "在线三次都失败" 1 "${rc}" "$(calls)" \
    "offline online clean online clean online clean" "${out}"

out="$(run ok)"
rc=$?
check "命中完整缓存" 0 "${rc}" "$(calls)" "offline" "${out}"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
