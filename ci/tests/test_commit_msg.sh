#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_commit_msg.sh — ci/checks/commit_msg.py 的分支回归
#
# 真实仓库常年为绿，判定逻辑只能靠负例驱动：逐条造出超长主题、本机绝对
# 路径、低级措辞、空主题、合并检出与错误参数，断言只看退出码与判据名。
# 合规样例同时存在，否则「全判红」也能让负例全过。
#
# 用法: bash ci/tests/test_commit_msg.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
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

# fresh —— 每个用例一个干净仓，避免上一条提交进入区间。
fresh()
{
    rm -rf "${tmp}/repo"
    git init -q "${tmp}/repo"
    printf 'x\n' >"${tmp}/repo/seed"
    git -C "${tmp}/repo" add seed
    git -C "${tmp}/repo" -c user.name=CI -c user.email=ci@example.invalid \
        commit -q -m "ci: seed the fixture" >/dev/null
}

# commit MSG —— 以给定信息提交一条空提交；空串走允许空信息的分支。
commit()
{
    if [ -n "$1" ]; then
        git -C "${tmp}/repo" -c user.name=CI -c user.email=ci@example.invalid \
            commit -q --allow-empty -m "$1" >/dev/null
    else
        git -C "${tmp}/repo" -c user.name=CI -c user.email=ci@example.invalid \
            commit -q --allow-empty --allow-empty-message -m "" >/dev/null
    fi
}

run()
{
    env -u LD_LIBRARY_PATH python3 "${ROOT}/ci/checks/commit_msg.py" \
        --range HEAD --repo "${tmp}/repo" "$@" 2>&1
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

fresh
commit "ci: add commit message gate"
out="$(run)"
check "合规主题判通过" 0 "$?" "${out}" "0 处不合规"

fresh
commit "ci: $(printf 'x%.0s' $(seq 1 80))"
out="$(run)"
check "超长主题判拒绝" 1 "$?" "${out}" "L002"

fresh
commit "ci: read the file from /home/alice/build"
out="$(run)"
check "家目录路径判拒绝" 1 "$?" "${out}" "P001"
sha="$(git -C "${tmp}/repo" rev-parse HEAD | cut -c1-9)"
case "${out}" in
*"P001 ${sha}"*) ok "违规行带提交哈希" ;;
*) bad "违规行带提交哈希（缺「P001 ${sha}」）"; printf '%s\n' "${out}" >&2 ;;
esac

fresh
commit "ci: write the artifact under D:\\out"
out="$(run)"
check "盘符路径判拒绝" 1 "$?" "${out}" "P001"

fresh
commit "fix: 修正拼写错误"
out="$(run)"
check "低级措辞判拒绝" 1 "$?" "${out}" "P002"

fresh
commit ""
out="$(run)"
check "空主题判拒绝" 1 "$?" "${out}" "L001"

# PR 检出的 HEAD 是平台生成的合并提交，--no-merges 会把它滤掉；判据必须
# 落到第二个父提交（PR 头提交）上，否则这条腿只是空跑。
fresh
base="$(git -C "${tmp}/repo" symbolic-ref --short HEAD)"
git -C "${tmp}/repo" checkout -q -b topic
commit "feat: 从 /home/bob/src 取件"
git -C "${tmp}/repo" checkout -q "${base}"
git -C "${tmp}/repo" -c user.name=CI -c user.email=ci@example.invalid \
    merge -q --no-ff -m "Merge branch 'topic'" topic
out="$(run)"
check "合并检出判 PR 头提交" 1 "$?" "${out}" "P001"

fresh
out="$(run --max-bytes 0)"
check "非法上限退 5" 5 "$?" "${out}" "commit-msg:"

fresh
out="$(env -u LD_LIBRARY_PATH python3 "${ROOT}/ci/checks/commit_msg.py" \
    --range HEAD --repo "${tmp}/absent" 2>&1)"
check "非仓库退 5" 5 "$?" "${out}" "commit-msg:"

if [ "${fails}" -ne 0 ]; then
    printf 'test_commit_msg: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_commit_msg: 全部通过\n'
