#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_workflow_rules.sh — ci/checks/check_workflows.py 规则 13–20 的负向回归
#
# 这几条规则此前只有正向：真实树常年合规，规则写错、判反或者根本没接进
# main() 都不会有人发现。本文件在一棵最小假树上逐条破坏，断言只看退出码
# 与稳定判据字符串，因此不依赖真实 workflow 的数量与预算。
#
# 真实树是否合规不在这里判：os-chk-verify 直接跑 ci/checks/check_workflows.py
# 那一条，本文件只保证这几条规则既不漏判也不误判。
#
# 用法: bash ci/tests/test_workflow_rules.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
SHA=11bd71901bbe5b1630ceea73d27597364c9af683
fails=0

mkdir -p "${FX}/ci/checks" "${FX}/ci/baseline" "${FX}/.github/workflows" "${FX}/tools/nightly"
cp "${ROOT}/ci/checks/check_workflows.py" "${FX}/ci/checks/check_workflows.py"
# check_gate_libs 无条件读 ci/ci.sh；本文件不判门禁库，留空文件即可。
: >"${FX}/ci/ci.sh"
# gate_callers 无条件读 ci/lint.sh；空文件等价于「lint.sh 没有调用任何门禁」。
: >"${FX}/ci/lint.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    python3 "${FX}/ci/checks/check_workflows.py" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

# reset — 清掉本用例写过的 workflow 与说明面，预算表由 flow 重写。
reset()
{
    rm -rf "${FX}/.github"
    mkdir -p "${FX}/.github/workflows"
    rm -f "${FX}/ci/baseline/gate-owners.tsv"
    printf 'runner:\n  label: ubuntu-24.04\n  hosted_ceiling_minutes: 360\n' \
        >"${FX}/tools/nightly/manifest.yaml"
}

# doc [规则号列表] — 写第 4 节规则表；默认登记 1..20，与脚本自述一致。
doc()
{
    local nums="${1:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20}"

    {
        printf '## 4. 命名与结构约定\n\n| # | 规则 | 为什么 |\n|---|------|--------|\n'
        printf '| %s | 全部规则 | 占位 |\n\n## 5. 其余\n' "${nums}"
    } >"${FX}/.github/workflows/README.md"
}

# flow <文件> <运行器> <uses 引用> <uses 注释> <pr|push>
# 其余字段固定为合规形态：命名同名词、显式权限、job 超时、无内联块。
flow()
{
    local file="$1" runner="$2" ref="$3" note="$4" trig="$5"

    {
        printf 'name: %s\n' "${file%.yml}"
        printf 'on:\n  push:\n    paths:\n      - %s\n' "'ci/**'"
        if [ "${trig}" = pr ]; then
            printf '  pull_request:\n    paths:\n      - %s\n' "'ci/**'"
        fi
        printf 'permissions:\n  contents: read\n'
        if [ "${trig}" = pr ]; then
            printf 'concurrency:\n  group: ci\n  cancel-in-progress: true\n'
        fi
        printf 'jobs:\n  gate:\n    runs-on: %s\n' "${runner}"
        printf '    timeout-minutes: 30\n    steps:\n'
        printf '      - uses: %s%s\n' "${ref}" "${note}"
        printf '      - run: echo ok\n'
    } >"${FX}/.github/workflows/${file}"
    printf '%s 0 1\n' "${file}" >"${FX}/ci/baseline/workflow-budgets.txt"
}

reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "基线：合规树通过" 0 "$?" "${out}" "检查通过"

reset
doc
flow demo-verify.yml ubuntu-latest "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 13：浮动运行器标签" 1 "$?" "${out}" "未钉版"

reset
doc
flow demo.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 14：非生命周期文件名" 1 "$?" "${out}" "须取生命周期名"

reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" push
out=$(run)
check "规则 14：-verify 不由 PR 触发" 1 "$?" "${out}" "不由 pull_request 触发"

reset
doc 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,20
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 15：规则表漏登" 1 "$?" "${out}" "规则 19 未出现"

reset
doc 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 15：规则表多登" 1 "$?" "${out}" "不存在的规则 21"

reset
doc 1,2,3,4,5,6,7,8,9,10,11,12,13,13,14,15,16,17,18
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 15：规则表重号" 1 "$?" "${out}" "重号 13"

reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@v4" "" pr
out=$(run)
check "规则 16：action 钉浮动 tag" 1 "$?" "${out}" "未钉到 40 位提交 SHA"

reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" "" pr
out=$(run)
check "规则 16：钉 SHA 缺版本注释" 1 "$?" "${out}" "缺少 \`# <版本>\` 注释"

reset
doc
flow demo-verify.yml ubuntu-22.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 17：runs-on 与清单 label 不一致" 1 "$?" "${out}" "runner.label=ubuntu-24.04 不一致"

reset
doc
rm -f "${FX}/tools/nightly/manifest.yaml"
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 17：清单缺 runner.label" 1 "$?" "${out}" "未登记 runner.label"

reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 17：两侧一致时通过" 0 "$?" "${out}" "检查通过"

# 多架构矩阵：runs-on 指向 matrix.runner 时，登记判据落到 include 的取值上。
# 集合登记在 manifest 的 runner.labels（label 是它的单架构子集）。
matrix_labels()
{
    printf 'runner:\n  label: ubuntu-24.04\n  labels:\n    - ubuntu-24.04\n    - ubuntu-24.04-arm\n  hosted_ceiling_minutes: 360\n' \
        >"${FX}/tools/nightly/manifest.yaml"
}

# flowm <文件> <矩阵 runner 取值> — 一个两腿矩阵 job，其余字段合规。
flowm()
{
    local file="$1" runner="$2"

    {
        printf 'name: %s\n' "${file%.yml}"
        printf 'on:\n  push:\n    paths:\n      - %s\n' "'ci/**'"
        printf '  pull_request:\n    paths:\n      - %s\n' "'ci/**'"
        printf 'permissions:\n  contents: read\n'
        printf 'concurrency:\n  group: ci\n  cancel-in-progress: true\n'
        printf 'jobs:\n  gate:\n    strategy:\n      matrix:\n        include:\n'
        printf '          - arch: x86_64\n            runner: ubuntu-24.04\n'
        printf '          - arch: aarch64\n            runner: %s\n' "${runner}"
        printf '    runs-on: ${{ matrix.runner }}\n'
        printf '    timeout-minutes: 30\n    steps:\n'
        printf '      - uses: actions/checkout@%s # v4\n' "${SHA}"
    } >"${FX}/.github/workflows/${file}"
    printf '%s 0 1\n' "${file}" >"${FX}/ci/baseline/workflow-budgets.txt"
}

reset
doc
matrix_labels
flowm demo-verify.yml ubuntu-24.04-arm
out=$(run)
check "规则 17：矩阵取值全部已登记时通过" 0 "$?" "${out}" "检查通过"

reset
doc
matrix_labels
flowm demo-verify.yml ubuntu-22.04-arm
out=$(run)
check "规则 17：矩阵取值为未登记架构标签" 1 "$?" "${out}" "运行器 ubuntu-22.04-arm"

reset
doc
matrix_labels
flowm demo-verify.yml ubuntu-latest
out=$(run)
check "规则 13：矩阵取值为浮动标签" 1 "$?" "${out}" "未钉版"

reset
doc
mkdir -p "${FX}/tools"
printf 'def hosted(tag):\n    return tag.endswith("-latest")\n' >"${FX}/tools/scan.py"
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 18：工具里由标签形状判托管" 1 "$?" "${out}" "endswith('-latest')"

reset
doc
mkdir -p "${FX}/ci/gates"
printf 'case "${label}" in\n    *-latest) hosted=1 ;;\nesac\n' \
    >"${FX}/ci/gates/scan.sh"
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
out=$(run)
check "规则 18：门禁里用 *-latest 匹配" 1 "$?" "${out}" "case 匹配 *-latest"
rm -rf "${FX}/tools" "${FX}/ci/gates"

# 规则 19：remote-sync 的命名空间只能由调用方写明。缺 --kind 时门禁按
# heads 查，标签派发会去查不存在的同名分支，判负成「无此分支」。
# 前一条用例删掉了 tools/ 与 ci/gates/，这里补齐规则 12 与 17 的对照基准，
# 让正例只受规则 19 影响。
mkdir -p "${FX}/tools/nightly" "${FX}/ci/gates"
printf '#!/bin/sh\n# 用法: ci/gates/remote-sync.sh\n' \
    >"${FX}/ci/gates/remote-sync.sh"
reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
printf '      - run: ci/ci.sh remote-sync --ref v0.0.4\n' \
    >>"${FX}/.github/workflows/demo-verify.yml"
printf 'demo-verify.yml 1 1\n' >"${FX}/ci/baseline/workflow-budgets.txt"
out=$(run)
check "规则 19：remote-sync 缺 --kind" 1 "$?" "${out}" "未声明 --kind"

reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
printf '      - run: ci/ci.sh remote-sync --kind tags --ref v0.0.4\n' \
    >>"${FX}/.github/workflows/demo-verify.yml"
printf 'demo-verify.yml 1 1\n' >"${FX}/ci/baseline/workflow-budgets.txt"
out=$(run)
check "规则 19：显式 --kind 时通过" 0 "$?" "${out}" "检查通过"

# 规则 20：每个门禁须有调用方或登记；登记项须指向真实门禁，列数恰好三列。
# 夹具里的门禁只放用法头：判据看的是「谁引用它」，脚本内容不参与。
gate()
{
    local name="$1"

    mkdir -p "${FX}/ci/gates"
    printf '#!/bin/sh\n# 用法: ci/gates/%s.sh\n' "${name}" \
        >"${FX}/ci/gates/${name}.sh"
}

# owners <行>... — 原样写入登记表；列数与分隔符由调用方给出的字面量决定。
owners()
{
    mkdir -p "${FX}/ci/baseline"
    printf '%s\n' "$@" >"${FX}/ci/baseline/gate-owners.tsv"
}

rm -rf "${FX}/ci/gates"
reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
gate orphan.sh
out=$(run)
check "规则 20：无调用方且未登记" 1 "$?" "${out}" "无调用方"

rm -rf "${FX}/ci/gates"
reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
owners "$(printf 'ghost\tmanual\t改名后遗留')"
out=$(run)
check "规则 20：登记了不存在的门禁" 1 "$?" "${out}" \
    "没有对应的 ci/gates/ghost.sh"

rm -rf "${FX}/ci/gates"
reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
gate manual-size
owners "$(printf 'manual-size\tmanual\t需发行机构建两轮对照')"
out=$(run)
check "规则 20：已登记的真实门禁放行" 0 "$?" "${out}" "检查通过"

rm -rf "${FX}/ci/gates"
reset
doc
flow demo-verify.yml ubuntu-24.04 "actions/checkout@${SHA}" " # v4" pr
gate manual-size
owners "$(printf 'manual-size\tmanual')"
out=$(run)
check "规则 20：登记列数不为 3" 1 "$?" "${out}" "列数须为 3"

if [ "${fails}" -ne 0 ]; then
    printf 'test_workflow_rules: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_workflow_rules: 全部通过\n'
