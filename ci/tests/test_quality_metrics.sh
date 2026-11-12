#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_quality_metrics.sh — ci/quality_metrics.py 的判据回归
#
# 棘轮门禁对真实树是绿的（基线按现状登记），判据是否正确只能靠负例驱动：
# 新增超标函数、新增重复片段、基线被抬高三种情形各造一次，另验 tests/ 与
# fixture/ 的负例目录不被计量。断言只看退出码与稳定判据名，不看措辞。
#
# 真实树的度量数字不在这里判：流水线的 ci/ci.sh lint 负责那一条。
#
# 用法: bash ci/tests/test_quality_metrics.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TOOL="${ROOT}/ci/quality_metrics.py"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
BASE="${tmp}/base.json"
fails=0

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
    python3 "${TOOL}" --dir "${FX}" --base "${BASE}" "$@" 2>&1
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

check_absent()
{
    local name="$1" got="$2" out="$3" needle="$4"

    if [ "${got}" != "0" ]; then
        bad "${name}（rc=${got}，期望 0）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) bad "${name}（不应出现「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    *) ok "${name}" ;;
    esac
}

seed()
{
    rm -rf "${FX}" "${BASE}"
    mkdir -p "${FX}"
    cat >"${FX}/a.sh" <<'EOF'
#!/usr/bin/env bash
hello()
{
    echo hello
}
EOF
}

# 11 个分支：越过单函数上限 10，且是新增函数名，必须判红。
busy()
{
    cat >"${FX}/busy.sh" <<'EOF'
#!/usr/bin/env bash
busy()
{
    local x="$1"
    if [ "${x}" = 1 ]; then echo 1; fi
    if [ "${x}" = 2 ]; then echo 2; fi
    if [ "${x}" = 3 ]; then echo 3; fi
    if [ "${x}" = 4 ]; then echo 4; fi
    if [ "${x}" = 5 ]; then echo 5; fi
    if [ "${x}" = 6 ]; then echo 6; fi
    if [ "${x}" = 7 ]; then echo 7; fi
    if [ "${x}" = 8 ]; then echo 8; fi
    if [ "${x}" = 9 ]; then echo 9; fi
    if [ "${x}" = 10 ]; then echo 10; fi
    if [ "${x}" = 11 ]; then echo 11; fi
}
EOF
}

# 两文件共享同一段 6 行：跨文件的复制粘贴，重复窗口数 +1。
dup()
{
    local body='v1=alpha
v2=beta
v3=gamma
v4=delta
v5=epsilon
v6=zeta'
    printf '%s\n' "${body}" >"${FX}/p.sh"
    printf 'echo head\n%s\necho tail\n' "${body}" >"${FX}/q.sh"
}

seed
out="$(run --update)"
check "首次建档可写入基线" 0 "$?" "${out}" "基线已写入"
out="$(run)"
check "基线内判定通过" 0 "$?" "${out}" "棘轮通过"

busy
out="$(run)"
check "新增超标函数判拒绝" 1 "$?" "${out}" "quality/cc: "
out="$(run --update)"
check "基线拒绝登记新超标函数" 1 "$?" "${out}" "只降不升"

seed
out="$(run)"
check "去掉超标函数后仍通过" 0 "$?" "${out}" "棘轮通过"

seed
mkdir -p "${FX}/tests" "${FX}/fixture"
busy
mv "${FX}/busy.sh" "${FX}/tests/heavy.sh"
cp "${FX}/tests/heavy.sh" "${FX}/fixture/heavy.sh"
out="$(run)"
check_absent "tests 与 fixture 不参与计量" "$?" "${out}" "heavy"

seed
run --update >/dev/null
dup
out="$(run)"
check "新增重复片段判拒绝" 1 "$?" "${out}" "quality/dup: "
out="$(run --update)"
check "基线拒绝登记新重复片段" 1 "$?" "${out}" "只降不升"

# python 走 AST：注释与文档串里的关键字不算判定点，函数体里的分支算。
seed
cat >"${FX}/m.py" <<'EOF'
"""模块文档：if for while 都不该被计数。"""


def plain(x):
    # if for while 在注释里也不该被计数
    return x


def branchy(x):
    if x == 1:
        return 1
    if x == 2:
        return 2
    if x == 3:
        return 3
    if x == 4:
        return 4
    if x == 5:
        return 5
    if x == 6:
        return 6
    if x == 7:
        return 7
    if x == 8:
        return 8
    if x == 9:
        return 9
    if x == 10:
        return 10
    if x == 11:
        return 11
    return 0
EOF
out="$(run --update)"
check "python 建档可写入基线" 0 "$?" "${out}" "基线已写入"
out="$(run)"
check "python 超标函数按 AST 登记后通过" 0 "$?" "${out}" "棘轮通过"

if [ "${fails}" -eq 0 ]; then
    printf 'ok   %s\n' "quality-metrics 回归全部通过"
else
    printf 'FAIL %s\n' "quality-metrics 回归失败 ${fails} 项"
fi
[ "${fails}" -eq 0 ]
