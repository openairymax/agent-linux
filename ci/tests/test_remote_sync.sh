#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_remote_sync.sh — 多远程同 sha 检查的分支回归
#
# 这道门禁的四种结论（一致、落后、无此分支、不可达）在页面上都是几行字，
# 唯独退出码决定派发能否继续。因此用两个本地裸仓构造出真实的分叉，逐条
# 断言输出与退出码，而不是只验一次「一致」。
#
# 用法: bash ci/tests/test_remote_sync.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

GATE="${ROOT}/ci/gates/remote-sync.sh"
export GIT_AUTHOR_NAME=airy GIT_AUTHOR_EMAIL=airy@example.invalid
export GIT_COMMITTER_NAME=airy GIT_COMMITTER_EMAIL=airy@example.invalid
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

git init -q --bare "${tmp}/one.git"
git init -q --bare "${tmp}/two.git"
git init -q "${tmp}/work"
git -C "${tmp}/work" remote add one "${tmp}/one.git"
git -C "${tmp}/work" remote add two "${tmp}/two.git"

commit()
{
    printf '%s\n' "$1" >"${tmp}/work/f.txt"
    git -C "${tmp}/work" add f.txt
    git -C "${tmp}/work" commit -q -m "$1"
}

# 分支名统一取 main，避开宿主 init.defaultBranch 的差异。
git -C "${tmp}/work" checkout -q -b main

check()
{
    local name="$1" want_rc="$2" text="$3"
    shift 3
    local out rc
    out="$(bash "${GATE}" --repo "${tmp}/work" "$@" 2>&1)"
    rc=$?
    if [ "${rc}" -ne "${want_rc}" ]; then
        printf 'FAIL %s: 退出码 %d，期望 %d\n%s\n' \
            "${name}" "${rc}" "${want_rc}" "${out}" >&2
        fails=$((fails + 1))
        return
    fi
    if [ -n "${text}" ] && ! printf '%s' "${out}" | grep -Fq -- "${text}"; then
        printf 'FAIL %s: 输出缺少 %s\n%s\n' "${name}" "${text}" "${out}" >&2
        fails=$((fails + 1))
        return
    fi
    printf 'ok   %s\n' "${name}"
}

commit first
git -C "${tmp}/work" push -q one main
git -C "${tmp}/work" push -q two main
check "两侧同 sha" 0 "全部远程同 sha"

# 只推一侧：这正是「本机有、镜像还没收」的形态。
commit second
git -C "${tmp}/work" push -q one main
check "单侧落后判负" 1 "落后"
check "落后时点名远程" 1 "two"

git -C "${tmp}/work" push -q two main
check "补齐后转绿" 0 "全部远程同 sha"

# 分支名只在一侧存在：派发到没有该分支的远程取不到源码。
git -C "${tmp}/work" branch -q side
git -C "${tmp}/work" push -q one side
check "缺分支判负" 1 "无此分支" --ref side

# 远程不可达不能当作通过：断网时若判过，这道门禁就形同不存在。
git -C "${tmp}/work" remote add gone "${tmp}/absent.git"
check "不可达判负" 1 "不可达" --remote gone --timeout 5
# 后续用例走缺省远程集合，留着一个不可达的远程会掩盖真正要断言的那一项。
git -C "${tmp}/work" remote remove gone

check "指定远程只看指定项" 0 "ok" --remote one
check "仓库不存在退 5" 5 "不是 git 仓库" --repo "${tmp}/absent"

# 发版走标签：注解标签在远程上是标签对象，构建用的是它剥出的提交。若只比
# 标签对象，两侧都会「一致」而实际提交不同，所以这里钉死提交 sha 再比。
git -C "${tmp}/work" tag -a v0 -m v0 >/dev/null
check "标签只在单侧" 1 "无此分支" --kind tags --ref v0
git -C "${tmp}/work" push -q one v0
git -C "${tmp}/work" push -q two v0
check "注解标签剥到提交" 0 "全部远程同 sha" --kind tags --ref v0 \
    --sha "$(git -C "${tmp}/work" rev-parse HEAD)"
check "非法 kind 退 5" 5 "--kind 只能是" --kind both

if [ "${fails}" -eq 0 ]; then
    printf 'test_remote_sync: 全部通过\n'
else
    printf 'test_remote_sync: %d 项失败\n' "${fails}" >&2
fi
exit "$((fails > 0))"
