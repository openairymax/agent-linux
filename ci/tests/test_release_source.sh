#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_source.sh — ci/gates/release-source.sh 的分支回归
#
# 判定逻辑要证明的是「子仓源码真的进了归档」，而真实仓库有 1.4 GB 内核树，
# 单测里不可能反复打包。这里造一棵超项目 + 两个子仓的夹具：成员布局
# （Makefile、ci/ci.sh、子仓内核树）按真实仓库的形状裁剪，够触发全部判据。
#
# 关键回归是「子仓漏导」：旧做法只对超项目 git archive，归档看着正常，
# 解包却没有子仓。夹具里断言子仓文件必须在成员列表里，并用未初始化的
# 子仓反证门禁会判负而不是静默导出半份源码。
#
# 用法: bash ci/tests/test_release_source.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/release-source.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0
EPOCH=1700000000

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

gitc()
{
    git -c user.name=t -c user.email=t@example.invalid \
        -c protocol.file.allow=always "$@" >/dev/null 2>&1
}

# 子仓：kernel 里带一个深层头文件，其余子仓一个文本文件即可。
mk_repo()
{
    local dir="$1"
    shift

    mkdir -p "${dir}"
    gitc -C "${dir}" init -q -b main
    while [ "$#" -gt 0 ]; do
        mkdir -p "${dir}/$(dirname "$1")"
        printf 'fixture\n' >"${dir}/$1"
        shift
    done
    gitc -C "${dir}" add -A
    gitc -C "${dir}" commit -q -m init
}

mk_repo "${tmp}/kernel" Makefile include/linux/sched.h
mk_repo "${tmp}/services" README.md
mk_repo "${tmp}/super" Makefile ci/ci.sh
gitc -C "${tmp}/super" submodule add -q "${tmp}/kernel" kernel
gitc -C "${tmp}/super" submodule add -q "${tmp}/services" services
gitc -C "${tmp}/super" commit -q -m "add submodules"

run()
{
    SOURCE_DATE_EPOCH="${EPOCH}" bash "${GATE}" "$@" 2>&1
}

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

expect "超项目 + 子仓成包" 0 "自证通过" \
    snapshot --ver 0.0.4 --repo "${tmp}/super" --out "${tmp}/a"
expect "再次导出成功" 0 "" \
    snapshot --ver 0.0.4 --repo "${tmp}/super" --out "${tmp}/b"

tarx="${tmp}/a/agent-linux-0.0.4.tar.xz"
if cmp -s "${tarx}" "${tmp}/b/agent-linux-0.0.4.tar.xz"; then
    ok "两次导出逐字节一致"
else
    bad "两次导出不一致（确定性被破坏）"
fi

list="$(tar -tf "${tarx}")"
for want in agent-linux-0.0.4/ci/ci.sh \
            agent-linux-0.0.4/kernel/include/linux/sched.h \
            agent-linux-0.0.4/services/README.md \
            agent-linux-0.0.4/SOURCES; do
    printf '%s\n' "${list}" | grep -qxF "${want}" && continue
    bad "归档缺少 ${want}"
done
ok "子仓内容与 SOURCES 均在归档内"

printf '%s\n' "${list}" | grep -q '/\.git/' &&
    bad "归档混入 .git 目录" || ok "归档不含 .git"

srcs="$(tar -xOf "${tarx}" agent-linux-0.0.4/SOURCES)"
sub="$(git -C "${tmp}/super" rev-parse HEAD:kernel)"
printf '%s' "${srcs}" | grep -qxF "agent-linux $(git -C "${tmp}/super" rev-parse HEAD)" &&
    ok "SOURCES 记录了超项目提交" || bad "SOURCES 缺超项目提交"
printf '%s' "${srcs}" | grep -qxF "${sub} kernel" &&
    ok "SOURCES 记录了子仓 gitlink 提交" || bad "SOURCES 缺子仓提交"

# 未初始化的子仓：git submodule status 以 `-` 开头，必须判负而不是少导出。
gitc -C "${tmp}/super" submodule deinit -q -f services
expect "子仓未初始化即判负" 1 "子仓未初始化" \
    snapshot --ver 0.0.4 --repo "${tmp}/super" --out "${tmp}/c"

expect "缺 --ver 退出 5" 5 "缺少 --ver" snapshot --out "${tmp}/d"
expect "未知子命令退出 5" 5 "未知子命令" freeze --ver 0.0.4

if [ "${fails}" -gt 0 ]; then
    printf '\n%d 项断言失败\n' "${fails}"
    exit 1
fi
printf '\nrelease-source: 全部断言通过\n'
