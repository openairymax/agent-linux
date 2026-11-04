#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_abi.sh — ci/gates/release-abi.sh 的分支回归
#
# 门禁真正调用的是 tools/abi-check（依赖内核 UAPI 头与 SSoT 文档），单测不
# 复现那套判定，只用假工具喂三种结局：全通过、退出非零、判定行残缺。要盯住
# 的是门禁自己的责任——判定行是否被读对、失败时是否**真的**不产出报告、
# 工具打印的绝对路径是否被相对化、同一提交两次生成是否逐字节相同。
#
# 「失败仍留报告」是这里最值得断言的一条：报告是发布页上 ABI 已核验的凭据，
# 一份写着 failed 的报告被签了名传上去，比没有报告更能误导取件方。
#
# 用法: bash ci/tests/test_release_abi.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/release-abi.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0
EPOCH=1700000000

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/tools/abi-check/contract"
cp "${GATE}" "${FX}/ci/gates/release-abi.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

# 假工具按 FAKE_MODE 给结局；Sources 段故意打印绝对路径，用于验证相对化。
cat >"${FX}/tools/abi-check/abi_check.py" <<'PY'
import os
import sys

root = sys.argv[sys.argv.index("--repo-root") + 1]
mode = os.environ.get("FAKE_MODE", "pass")
print("agent-linux ABI Stability Check Report")
print("Sources:")
print("  unistd.h        : %s/kernel/include/uapi/asm-generic/unistd.h" % root)
print("  baseline        : %s/tools/abi-check/abi_baseline.json" % root)
if mode == "fail":
    print("Checks: 9/12 passed, 3 failed")
    sys.exit(1)
if mode == "partial":
    print("Checks: 10/12 passed, 2 failed")
    sys.exit(0)
print("Checks: 12/12 passed, 0 failed")
PY

printf '{}\n' >"${FX}/tools/abi-check/abi_baseline.json"
printf '{}\n' >"${FX}/tools/abi-check/contract/syscall_registry.snapshot.json"

# 溯源行要求真实提交：夹具本身就是一棵只有一次提交的仓库。
git -C "${FX}" init -q
git -C "${FX}" -c user.name=t -c user.email=t@e.invalid add -A >/dev/null 2>&1
git -C "${FX}" -c user.name=t -c user.email=t@e.invalid \
    commit -q -m fixture >/dev/null 2>&1

run()
{
    ( cd "${tmp}" && SOURCE_DATE_EPOCH="${EPOCH}" \
        bash "${FX}/ci/gates/release-abi.sh" "$@" 2>&1 )
}

# expect —— 单次调用的退出码与输出关键判据。
expect()
{
    local name="$1" want="$2" needle="$3"
    shift 3
    local out rc
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" != "${want}" ]; then
        bad "${name}（rc=${rc}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    if [ -n "${needle}" ] && ! printf '%s' "${out}" | grep -qF "${needle}"; then
        bad "${name}（输出缺少「${needle}」）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    ok "${name}"
}

head_sha="$(git -C "${FX}" rev-parse HEAD)"
expect "生成 ABI 报告" 0 "agent-linux-0.0.4-abi-report.md" \
    report --ver 0.0.4 --out "${tmp}/a"
RPT="${tmp}/a/agent-linux-0.0.4-abi-report.md"

if [ -f "${RPT}" ]; then
    ok "报告文件存在"
else
    bad "报告文件缺失"
fi

for needle in '# AirymaxOS v0.0.4 syscall ABI 报告' \
              '- 判定: PASS（12/12 passed, 0 failed）' \
              "- 超项目提交: ${head_sha}" \
              '- 生成时间: 2023-11-14T22:13:20Z' \
              '```text'; do
    if grep -qF -- "${needle}" "${RPT}"; then
        ok "报告含「${needle}」"
    else
        bad "报告缺少「${needle}」"
    fi
done

# 绝对路径进发布物等于把构建机目录结构带上发布页，也让同一份源码在不同
# 机器上产出不同字节。
if grep -qF "${FX}" "${RPT}"; then
    bad "报告仍含绝对路径 ${FX}"
else
    ok "工具输出的绝对路径已相对化"
fi

run report --ver 0.0.4 --out "${tmp}/b" >/dev/null 2>&1
if cmp -s "${RPT}" "${tmp}/b/agent-linux-0.0.4-abi-report.md"; then
    ok "同一提交两次生成逐字节一致"
else
    bad "两次生成的报告不一致（确定性被破坏）"
fi

FAKE_MODE=fail expect "检查失败即判负" 1 "ABI 检查失败" \
    report --ver 0.0.4 --out "${tmp}/c"
if [ -e "${tmp}/c/agent-linux-0.0.4-abi-report.md" ]; then
    bad "检查失败仍产出了报告"
else
    ok "检查失败不产出报告"
fi

FAKE_MODE=partial expect "判定行残缺即判负" 1 "全通过判定行" \
    report --ver 0.0.4 --out "${tmp}/d"
if [ -e "${tmp}/d/agent-linux-0.0.4-abi-report.md" ]; then
    bad "判定行残缺仍产出了报告"
else
    ok "判定行残缺不产出报告"
fi

expect "缺 --ver 退出 5" 5 "缺少 --ver" report --out "${tmp}/e"
expect "未知子命令退出 5" 5 "未知子命令" freeze --ver 0.0.4

if [ "${fails}" -gt 0 ]; then
    printf '\n%d 项断言失败\n' "${fails}"
    exit 1
fi
printf '\nrelease-abi: 全部断言通过\n'
