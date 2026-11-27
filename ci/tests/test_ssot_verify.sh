#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_ssot_verify.sh — ssot-verify 的 links 分支回归夹具
#
# 门禁按仓库根的 git 索引扫 Markdown 相对链接。夹具把门禁与它的库复制进
# 一棵临时仓库，使 ROOT 落在假树上：这样失效链接、锚点写法、跨仓相对
# 路径三类判据都能在本地逐条钉住，且不触碰真实仓库的文档。
#
# syntax 分支不在本夹具内：它要求真实注册表与治理脚本，属发布侧校验。
#
# 用法: bash ci/tests/test_ssot_verify.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
TREE="${tmp}/repo"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

run() { GITHUB_ACTIONS='' bash "${TREE}/ci/gates/ssot-verify.sh" "$@" 2>&1; }

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

mkdir -p "${TREE}/ci/gates" "${TREE}/ci/lib"
cp "${ROOT}/ci/gates/ssot-verify.sh" "${TREE}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${TREE}/ci/lib/"
(
    cd "${TREE}" || exit 1
    git init -q
    git config user.email fixture@example.invalid
    git config user.name fixture
    printf '# 根文档\n' >"README.md"
    printf '# 目标\n' >"target.md"
    printf '%s\n' '[同目录](target.md)' '[锚点](target.md#sec)' \
        '[跨仓](../external/doc.md)' >"README.md"
    git add -A
    git commit -qm fixture
)

out="$(run --kind links)"; rc=$?
check "全部链接可解析时通过" 0 "${rc}" "${out}" "本仓相对链接一致"

printf '%s\n' '[缺失](missing.md)' >"${TREE}/broken.md"
(
    cd "${TREE}" || exit 1
    git add broken.md
)
out="$(run --kind links)"; rc=$?
check "失效相对链接判负" 1 "${rc}" "${out}" "存在失效的本仓相对链接"
case "${out}" in
*"BROKEN LINK: broken.md"*) ok "判据指出失效文件" ;;
*) bad "判据未指出失效文件"; printf '%s\n' "${out}" >&2 ;;
esac

rm -f "${TREE}/broken.md"
(
    cd "${TREE}" || exit 1
    git add -A
)
out="$(run --kind links)"; rc=$?
check "删去失效链接后恢复通过" 0 "${rc}" "${out}" "本仓相对链接一致"

# 未跟踪的 Markdown 不参与扫描：门禁的判据面是 git 索引。
printf '%s\n' '[缺失](missing.md)' >"${TREE}/untracked.md"
out="$(run --kind links)"; rc=$?
check "未跟踪文档不参与扫描" 0 "${rc}" "${out}" "本仓相对链接一致"

out="$(run --kind nope)"; rc=$?
check "未知 kind 退 5" 5 "${rc}" "${out}" "用法:"

out="$(run)"; rc=$?
check "缺 --kind 退 5" 5 "${rc}" "${out}" "用法:"

out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

if [ "${fails}" -ne 0 ]; then
    printf 'test_ssot_verify: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_ssot_verify: 全部通过\n'
