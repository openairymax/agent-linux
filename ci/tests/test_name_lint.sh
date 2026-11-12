#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_name_lint.sh — ci/name_lint.py 的分支回归
#
# 门禁判的是「函数名不超过 15 字节」，真实树常年为绿，判定逻辑只能靠负例
# 驱动：逐条造出刚好 16 字节的定义、`function` 前缀写法、注释里的假定义、
# 测试目录里的长名，断言只看退出码与 N001 判据名。
#
# 真实树是否合规不在这里判：ci/lint.sh 负责那一条，本文件只保证门禁没有
# 漏判（长名被放过）与误判（注释、tests/ 目录被当成产品代码）。
#
# 用法: bash ci/tests/test_name_lint.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci" "${FX}/tools/pkg/tests"
cp "${ROOT}/ci/name_lint.py" "${FX}/ci/name_lint.py"

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
    ( cd "${FX}" && env -u LD_LIBRARY_PATH python3 ci/name_lint.py "$@" 2>&1 )
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

# 15 字节是合规上界：恰好 15 的定名必须放过，16 才是判负的起点。
tree()
{
    rm -rf "${FX}/tools/pkg"
    mkdir -p "${FX}/tools/pkg/tests"
    cat >"${FX}/tools/pkg/good.sh" <<'EOF'
#!/usr/bin/env bash
# 注释里的假定义 abcdefghijklmnop() 不算。
abcdefghijklmno()
{
    :
}

function ok_name ()
{
    :
}
EOF
}

tree
out="$(run tools)"
check "合规树判通过" 0 "$?" "${out}" "0 issue(s)"

tree
cat >"${FX}/tools/pkg/bad.sh" <<'EOF'
#!/usr/bin/env bash
abcdefghijklmnop()
{
    :
}
EOF
out="$(run tools)"
check "超长定名判拒绝" 1 "$?" "${out}" "N001 abcdefghijklmnop"

tree
cat >"${FX}/tools/pkg/bad.sh" <<'EOF'
#!/usr/bin/env bash
function abcdefghijklmnop
{
    :
}
EOF
out="$(run tools)"
check "function 前缀超长判拒绝" 1 "$?" "${out}" "bad.sh:2: N001"

tree
cat >"${FX}/tools/pkg/tests/case.sh" <<'EOF'
#!/usr/bin/env bash
abcdefghijklmnop()
{
    :
}
EOF
out="$(run tools)"
check "tests 目录内长名不计" 0 "$?" "${out}" "0 issue(s)"

tree
cat >"${FX}/tools/pkg/bad.sh" <<'EOF'
#!/usr/bin/env bash
abcdefghijklmnop() { :; }
EOF
out="$(run tools)"
check "单行定义同样判拒绝" 1 "$?" "${out}" "N001"

tree
out="$(run nope)"
check "根不存在跳过不假绿" 0 "$?" "${out}" "0 issue(s)"

tree
printf 'abcdefghijklmnop() { :; }\n' >"${FX}/tools/pkg/bad.py"
out="$(run tools)"
check "非 shell 后缀不纳入" 0 "$?" "${out}" "0 issue(s)"
rm -f "${FX}/tools/pkg/bad.py"

tree
printf 'ok_name() { :; }\n' >"${FX}/ci/a.sh"
out="$(run)"
check "缺省根扫描 ci 与 tools" 0 "$?" "${out}" "name: 2 file(s)"

if [ "${fails}" -ne 0 ]; then
    printf 'test_name_lint: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_name_lint: 全部通过\n'
