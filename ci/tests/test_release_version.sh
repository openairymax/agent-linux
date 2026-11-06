#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_version.sh — ci/gates/release-version.sh 的分支回归
#
# tag 有两个必须同时成立的判据：格式合法，且等于树内声明的版本。0.0.3 的
# 发布链只推导 tag 与最高版本，给旧树打新 tag 也能整轮跑完，产物名字与包内
# 版本各说各话，直到装机才发现 os-release、内核 vendor 宏与 Release 对不上。
# 本回归用一棵只带 distro/release/os-release 的假树跑门禁真身，覆盖补 v 前缀、
# -rcN 共享基线、以及三种不同源形态：版本更高、版本更低、来源缺失。
#
# 用法: bash ci/tests/test_release_version.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
OS_RELEASE="${FX}/distro/release/os-release"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/release"
cp "${ROOT}/ci/gates/release-version.sh" "${FX}/ci/gates/release-version.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

tree()
{
    printf 'NAME="AirymaxOS"\n%s\n' "$1" >"${OS_RELEASE}"
}

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 门禁在 CI 里把 tag/ver 写进 $GITHUB_OUTPUT，本地才打印。回归断言的是
# 打印形态，若继承工作流的 GITHUB_OUTPUT，out_set 会安静地写进那个文件，
# 断言取不到文本——本地全过、CI 全红（run 6e31b4b）。输出通道属于运行
# 环境，回归必须自带干净环境，不能要求门禁为测试改行为。
run()
{
    env -u GITHUB_OUTPUT -u GITHUB_ENV -u GITHUB_ACTIONS \
        bash "${FX}/ci/gates/release-version.sh" "$@" 2>&1
}

# expect NAME RC TEXT [ARG...] —— 跑一次门禁，比对退出码与输出片段。
expect()
{
    local name="$1" want_rc="$2" want_text="$3" out rc
    shift 3
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" != "${want_rc}" ]; then
        bad "${name}（rc=${rc}，期望 ${want_rc}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${want_text}" ] && ! printf '%s' "${out}" | grep -qF -- "${want_text}"; then
        bad "${name}（输出缺「${want_text}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

tree 'VERSION_ID="0.0.4"'
expect "带 v 前缀且同源" 0 "ver=0.0.4" --tag v0.0.4
expect "缺 v 前缀自动补齐" 0 "tag=v0.0.4" --tag 0.0.4
expect "-rc 共享基线版本" 0 "ver=0.0.4-rc1" --tag v0.0.4-rc1
# 预发布标记由 tag 后缀推出，发布侧不再自己写一套口径。
expect "正式 tag 非预发布" 0 "pre_rel=false" --tag v0.0.4
expect "-rc 是预发布" 0 "pre_rel=true" --tag v0.0.4-rc1
expect "tag 高于树内版本" 1 "不同源" --tag v0.0.5
expect "tag 低于树内版本" 1 "不同源" --tag v0.0.3
expect "四段版本号非法" 1 "非法的发布 tag" --tag v0.0.4.1
expect "缺 tag" 5 "缺少发布 tag"

tree 'VERSION_ID="0.0.5"'
expect "树内版本先行更新" 0 "ver=0.0.5" --tag v0.0.5

tree '# 只有注释，没有 VERSION_ID'
expect "缺 VERSION_ID" 1 "未声明 VERSION_ID" --tag v0.0.4

rm -f "${OS_RELEASE}"
expect "缺版本声明文件" 1 "缺少版本声明" --tag v0.0.4

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
