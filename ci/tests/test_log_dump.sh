#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_log_dump.sh — ci/gates/log-dump.sh 的呈现契约回归
#
# 这条门禁本身不做判定，只把产物里的 .log 摊开；一旦摊开的顺序、分组头或
# 节流行数变了，失败现场的阅读方式就跟着变。夹具钉住四件事：参数错误退 5；
# 目录不存在与没有日志都只告警退 0；分组头按路径排序且剥掉 --dir 前缀；
# --lines 只保留尾部 N 行。
#
# 用法: bash ci/tests/test_log_dump.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/log-dump.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/logs/sub"
    cp "${ROOT}/ci/gates/log-dump.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
}

logs()
{
    seq 1 200 | sed 's/^/a-/' >"${FX}/logs/a.log"
    seq 1 200 | sed 's/^/b-/' >"${FX}/logs/b.log"
    seq 1 20 | sed 's/^/c-/' >"${FX}/logs/sub/c.log"
}

run()
{
    out="$(GITHUB_ACTIONS='' bash "${GATE}" "$@" 2>&1)"
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

has()
{
    local name="$1" line="$2"

    if printf '%s\n' "${out}" | grep -qxF -- "${line}"; then
        ok "${name}"
    else
        bad "${name}（缺行「${line}」）"
        printf '%s\n' "${out}" >&2
    fi
}

lacks()
{
    local name="$1" line="$2"

    if printf '%s\n' "${out}" | grep -qxF -- "${line}"; then
        bad "${name}（不应出现「${line}」）"
        printf '%s\n' "${out}" >&2
    else
        ok "${name}"
    fi
}

setup
run --help
check "帮助退 0" 0 "用法:"

run --bogus
check "未知参数退 5" 5 "未知参数"

run
check "缺 --dir 退 5" 5 "--dir 必填"

run --dir "${tmp}/nope"
check "目录不存在只告警" 0 "目录不存在"

setup
run --dir "${FX}/logs"
check "空目录只告警" 0 "没有 .log 文件"

logs
run --dir "${FX}/logs"
check "三份日志放行" 0 "=== a.log ==="
heads="$(printf '%s\n' "${out}" | grep '^=== ' | paste -sd'|')"
if [ "${heads}" = "=== a.log ===|=== b.log ===|=== sub/c.log ===" ]; then
    ok "分组头按路径排序并剥前缀"
else
    bad "分组头序列为「${heads}」"
    printf '%s\n' "${out}" >&2
fi
has "默认保留尾部第 120 行" "a-81"
lacks "默认裁掉第 121 行之前" "a-80"
has "短文件全量显示" "c-1"

run --dir "${FX}/logs" --lines 5
check "节流行数放行" 0 "=== a.log ==="
has "--lines 保留尾部末行" "a-200"
lacks "--lines 裁掉第 6 行之前" "a-195"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
