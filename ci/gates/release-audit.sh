#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-audit.sh — 发布页资产必须与本次物料完全一致
#
# 用法: ci/gates/release-audit.sh --tag TAG [--repo OWNER/NAME] [--dir DIR]
#
# 上传在 GitHub 侧没有事务：glob 里少一个文件、某个 PUT 被 502 重试吞掉、
# 或者上传步在 timeout 边缘被截断，发布页都会安静地少几件东西。此前只有
# release-prune 在写入之前删多余项，写入之后没有任何一步把页面读回来，
# 于是「发布成功」等价于「action-gh-release 没报错」——页面缺件无人发现。
#
# 判据双向：缺失（want 有、have 无）与多余（have 有、want 无）都判失败。
# 多余通常意味着 prune 被跳过或步骤次序放错，同样属于发布不完整。
#
# 退出码: 0 完全一致；1 缺失或多余；3 Release 不存在；5 参数与前置条件不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/release-assets.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/release-assets.sh"

REPO="${GITHUB_REPOSITORY:-}"
TAG=""
DIR="."

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)  TAG="${2:-}"; shift 2 ;;
        --repo) REPO="${2:-}"; shift 2 ;;
        --dir)  DIR="${2:-}"; shift 2 ;;
        -h|--help) sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)      err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TAG}" ] || { err "缺少 --tag"; exit "${EINVAL}"; }
[ -n "${REPO}" ] || { err "缺少 --repo（或 GITHUB_REPOSITORY）"; exit "${EINVAL}"; }
[ -d "${DIR}" ] || { err "目录不存在: ${DIR}"; exit "${EINVAL}"; }

# 对账要读的是写入后的真实页面，不能停在「拿不到就跳过」：匿名 GET 走
# runner 的共享出口 IP，配额（60 次/小时）随时被别人用光，失败会表现为
# 403 而不是缺件，正好掩盖它要查的东西。因此要求显式令牌，缺令牌直接退 5。
# 工作流注入的是 GITHUB_TOKEN，本地与回归用 TOKEN；三者取到即用，统一
# export 成库认识的 TOKEN。
TOKEN="${TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
[ -n "${TOKEN}" ] || { err "缺少 GITHUB_TOKEN"; exit "${EINVAL}"; }
export TOKEN

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

airy_assets_want "${DIR}" "${work}/want" || exit "${EINVAL}"

airy_assets_have "${REPO}" "${TAG}" "${work}/have" "${work}/rel.json"
rc=$?
case "${rc}" in
    0) ;;
    "${AIRY_ASSETS_ENOREL}") err "Release ${TAG} 不存在，发布未完成"; exit "${AIRY_ASSETS_ENOREL}" ;;
    *) exit 1 ;;
esac

awk '{ print $2 }' "${work}/have" | sort -u >"${work}/names"
comm -23 "${work}/want" "${work}/names" >"${work}/missing"
comm -13 "${work}/want" "${work}/names" >"${work}/extra"

if [ -s "${work}/missing" ]; then
    err "发布页缺少 $(wc -l <"${work}/missing" | tr -d ' ') 项:"
    sed 's/^/  missing: /' "${work}/missing" >&2
fi
if [ -s "${work}/extra" ]; then
    err "发布页多出 $(wc -l <"${work}/extra" | tr -d ' ') 项:"
    sed 's/^/  extra:   /' "${work}/extra" >&2
fi
if [ ! -s "${work}/missing" ] && [ ! -s "${work}/extra" ]; then
    say "发布页与本次物料一致: $(wc -l <"${work}/want" | tr -d ' ') 项（${TAG}）"
else
    exit 1
fi
