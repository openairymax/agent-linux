#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_wp_scope.sh — wp-scope 门禁的分支回归
#
# 判据读的是册子与真实提交，两者都在仓外或易变，只有夹具能稳定复现分支：
# 归属得上且路径在卡内、归属得上但路径出卡、取不到号（放行并留痕）、
# 子仓指针、以及册子或范围缺席这几种情形，逐条钉死退出码与判据名。
#
# 用法: bash ci/tests/test_wp_scope.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PY="${ROOT}/ci/checks/wp_scope.py"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0
REPO="${tmp}/repo"
PLAN="${tmp}/plan.md"

mkplan()
{
    {
        printf '| 任务 | 描述 | 产出物 | 净新增 LOC | 泳道 | 施工日 | 严格前驱 | 直接后继 | 状态 |\n'
        printf '|---|---|---|---|---|---|---|---|---|\n'
        printf '| S-01-01 | 服务面首片 | system/tui/crates/svc/src/net.rs | 50 | S1 | 2027-10-12 | — | S-01-02 | 未开工 |\n'
        printf '| K-01-01 | 内核首片 | tools/loose.sh | 50 | K1 | 2027-10-12 | — | K-01-02 | 未开工 |\n'
    } >"${PLAN}"
}

mkrepo()
{
    rm -rf "${REPO}"
    git init -q "${REPO}"
    git -C "${REPO}" config user.email t@example.invalid
    git -C "${REPO}" config user.name tester
    mkdir -p "${REPO}/system/tui/crates/svc/src"
    printf 'fn main() {}\n' >"${REPO}/system/tui/crates/svc/src/net.rs"
    printf 'fn main() {}\n' >"${REPO}/README.md"
    git -C "${REPO}" add -A
    git -C "${REPO}" commit -q -m "init"
}

spot()
{
    git -C "${REPO}" add -A
    git -C "${REPO}" commit -q --allow-empty -m "$1"
    tip="$(git -C "${REPO}" rev-parse HEAD)"
    base="$(git -C "${REPO}" rev-parse HEAD~1)"
}

run()
{
    out="$(python3 "${PY}" --root "${REPO}" --plan "${PLAN}" --range "${base}..${tip}" 2>&1)"
    code=$?
}

assert()
{
    local label="$1" want="$2" needle="$3"

    if [ "${code}" -eq "${want}" ] && printf '%s' "${out}" | grep -qF "${needle}"; then
        printf 'ok   %s\n' "${label}"
        return 0
    fi
    printf 'FAIL %s（rc=%s，期望 %s，判据「%s」）\n' "${label}" "${code}" "${want}" "${needle}"
    printf '%s\n' "${out}" >&2
    fails=$((fails + 1))
}

mkrepo
mkplan
printf 'fn main() {}\n' >>"${REPO}/system/tui/crates/svc/src/net.rs"
spot "wp: 首片

WP: S-01-01"
run
assert "卡内路径放行" 0 "0 处越界"

printf 'fn main() {}\n' >"${REPO}/system/tui/crates/svc/src/other.rs"
spot "wp: 越界

WP: S-01-01"
run
assert "卡外路径判负" 1 "不在产出物内"

printf 'x\n' >"${REPO}/ci-note.txt"
spot "wp: 无号"
run
assert "未归属默认放行" 0 "未归属"

# 子仓指针在主仓的形态是一条 160000 的 gitlink，路径只剩首段。单独起一个
# 仓：同一个仓里既有 system/ 下的文件又有 system 指针，索引本身就不合法。
PTR="${tmp}/ptr"
git init -q "${PTR}"
git -C "${PTR}" config user.email t@example.invalid
git -C "${PTR}" config user.name tester
printf 'x\n' >"${PTR}/README.md"
git -C "${PTR}" add -A
git -C "${PTR}" commit -q -m "init"
git -C "${PTR}" update-index --add --cacheinfo "160000,$(git -C "${PTR}" rev-parse HEAD),system"
git -C "${PTR}" commit -q -m "wp: 指针

WP: S-01-01"
out="$(python3 "${PY}" --root "${PTR}" --plan "${PLAN}" \
    --range "$(git -C "${PTR}" rev-parse HEAD~1)..$(git -C "${PTR}" rev-parse HEAD)" 2>&1)"
code=$?
assert "子仓指针按首段放行" 0 "0 处越界"

spot "wp: 前缀越界

WP: K-01-01"
run
assert "产出物前缀越界判负" 1 "前缀越界"

mark="$(git -C "${REPO}" rev-parse HEAD)"
out="$(python3 "${PY}" --root "${REPO}" 2>&1)"
code=$?
assert "册子缺席退 3" 3 "不适用"

out="$(python3 "${PY}" --root "${REPO}" --plan "${tmp}/absent.md" 2>&1)"
code=$?
assert "显式册子不存在退 5" 5 "施工图纸不存在"

out="$(python3 "${PY}" --root "${REPO}" --plan "${PLAN}" --range "${mark}..${mark}" 2>&1)"
code=$?
assert "空范围退 3" 3 "无提交"

out="$(python3 "${PY}" --root "${REPO}" --plan "${PLAN}" --range "no-such-rev" 2>&1)"
code=$?
assert "范围不可读退 5" 5 "不可读"

out="$(python3 "${PY}" --root "${REPO}" --plan "${PLAN}" --bogus 2>&1)"
code=$?
assert "未知参数退 5" 5 "未知参数"

printf '无表格\n' >"${tmp}/empty.md"
out="$(python3 "${PY}" --root "${REPO}" --plan "${tmp}/empty.md" 2>&1)"
code=$?
assert "册子无卡片退 5" 5 "没有可解析"

out="$(python3 "${PY}" --help 2>&1)"
code=$?
assert "帮助含用法行" 0 "用法:"

if [ "${fails}" -ne 0 ]; then
    printf 'test_wp_scope: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_wp_scope: 全部通过\n'
