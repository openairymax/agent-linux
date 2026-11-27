#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_pool_state.sh — pool-state 门禁回归夹具
#
# 钉住的判据：
#   负向：缺 --dir / 未知参数时退 5 且报错；空池不得伪造摘要（digest 为空）。
#   正向：摘要随包集合、补全标记、闭包清单变化，同名同长内容替换不改键；
#         设置 GITHUB_OUTPUT 时按三行写出 count/lock/digest。
#
# 用法: bash ci/tests/test_pool_state.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 门禁在 GITHUB_OUTPUT 存在时只写文件、不打印；Actions 给每一步都设了
# 这个变量，夹具必须显式清空才能读到标准输出。写文件的分支单独断言。
run() { GITHUB_OUTPUT='' bash "${ROOT}/ci/gates/pool-state.sh" "$@" 2>&1; }

# 从 "count=N lock=M digest=HEX" 里取字段
val() { printf '%s\n' "$2" | tr ' ' '\n' | sed -n "s/^$1=//p"; }

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

mkpool()
{
    local d="$1"
    rm -rf "${d}"
    mkdir -p "${d}"
}

# 负向：缺 --dir
out="$(run)"; rc=$?
check "缺 --dir 退 5" 5 "${rc}" "${out}" "error: --dir 必填"

# 负向：未知参数
out="$(run --bogus)"; rc=$?
check "未知参数退 5" 5 "${rc}" "${out}" "error: 未知参数: --bogus"

# 正向：帮助退 0
out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

# 负向：空池不产摘要
d1="${tmp}/empty"; mkpool "${d1}"
out="$(run --dir "${d1}")"; rc=$?
check "空池退 0" 0 "${rc}" "${out}" ""
[ "$(val count "${out}") $(val lock "${out}") $(val digest "${out}")" \
    = "0 0 " ] &&
    ok "空池 digest 为空" || bad "空池伪造了摘要"

# 正向：两个包 → count=2、64 位十六进制摘要
d2="${tmp}/two"; mkpool "${d2}"
printf 'alpha' >"${d2}/alpha-1.0-1.x86_64.rpm"
printf 'beta' >"${d2}/beta-2.0-1.x86_64.rpm"
out="$(run --dir "${d2}")"; rc=$?
check "两包退 0" 0 "${rc}" "${out}" "count=2"
dg2="$(val digest "${out}")"
printf '%s' "${dg2}" | grep -qE '^[0-9a-f]{64}$' &&
    ok "两包摘要为 sha256" || bad "两包摘要格式异常: ${dg2}"
[ "$(val lock "${out}")" = 0 ] && ok "无清单时 lock=0" || bad "无清单却 lock=1"

# 正向：同输入摘要稳定
out2="$(run --dir "${d2}")"
[ "$(val digest "${out2}")" = "${dg2}" ] &&
    ok "同输入摘要稳定" || bad "同输入摘要漂移"

# 正向：同名同长替换内容不改键（按名+长取键的契约）
printf 'ALPHA' >"${d2}/alpha-1.0-1.x86_64.rpm"
out3="$(run --dir "${d2}")"
[ "$(val digest "${out3}")" = "${dg2}" ] &&
    ok "同名同长不改键" || bad "同名同长却改了键"

# 正向：包集合变化 → 摘要变化
printf 'gamma' >"${d2}/gamma-3.0-1.x86_64.rpm"
out4="$(run --dir "${d2}")"
[ "$(val count "${out4}")" = 3 ] && ok "新增包计数为 3" || bad "新增包计数异常"
[ "$(val digest "${out4}")" != "${dg2}" ] &&
    ok "新增包改变摘要" || bad "新增包未改变摘要"

# 负向判据：补全标记未进摘要会让补齐轮撞回旧键
touch "${d2}/.seed-complete"
out5="$(run --dir "${d2}")"
[ "$(val digest "${out5}")" != "$(val digest "${out4}")" ] &&
    ok "补全标记改变摘要" || bad "补全标记未进入摘要"

# 负向判据：闭包清单未进摘要会让离线重放轮撞回旧键
printf 'pkg a\n' >"${d2}/closure-x.lock"
out6="$(run --dir "${d2}")"
[ "$(val lock "${out6}")" = 1 ] && ok "有清单时 lock=1" || bad "有清单却 lock=0"
[ "$(val digest "${out6}")" != "$(val digest "${out5}")" ] &&
    ok "清单出现改变摘要" || bad "清单出现未改变摘要"
printf 'pkg b\n' >"${d2}/closure-x.lock"
out7="$(run --dir "${d2}")"
[ "$(val digest "${out7}")" != "$(val digest "${out6}")" ] &&
    ok "清单内容改变摘要" || bad "清单内容未改变摘要"

# 正向：GITHUB_OUTPUT 三行写出
gf="${tmp}/ghout"; : >"${gf}"
GITHUB_OUTPUT="${gf}" bash "${ROOT}/ci/gates/pool-state.sh" --dir "${d2}" \
    >/dev/null 2>&1
[ "$(wc -l <"${gf}" | tr -d ' ')" = 3 ] &&
    ok "GITHUB_OUTPUT 写三行" || bad "GITHUB_OUTPUT 行数不对"
for k in count lock digest; do
    grep -qE "^${k}=" "${gf}" ||
        bad "GITHUB_OUTPUT 缺 ${k}"
done
grep -qE '^count=3$' "${gf}" && ok "GITHUB_OUTPUT 计数正确" ||
    bad "GITHUB_OUTPUT 计数错误"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
