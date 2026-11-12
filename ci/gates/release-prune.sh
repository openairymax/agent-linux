#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-prune.sh — 写入 Release 之前收回页面
#
# 用法: ci/gates/release-prune.sh --tag TAG [--repo OWNER/NAME] [--dir DIR] [--dry-run]
#
# 建 Release 的动作用 glob 上传，只增不删。内核包名里带 RPM release 号
# （airymaxos-kernel-core-0.0.3-5.x86_64.rpm），同一个 tag 重发一轮就多出一整套
# 新号的包，旧号包原地留存：v0.0.3 首轮发布后页面上并排躺着 34 个 -5 的包，
# 取件方无从判断哪一套对应当前 tag。tag 重指是本项目的常规动作（release.yml
# 的 concurrency 就是为它设的），所以清理不是一次性收尾，而是发布流程必须
# 自带的幂等性。
#
# 本门禁只删多余项，不判缺失：它跑在写入之前，此刻缺件是正常的。写入之后
# 的完整性由 release-audit.sh 把页面读回来对账，两者共用 ci/lib/release-assets.sh
# 的期望集，避免各算一套。
#
# 退出码: 0 一致或无可删；1 接口失败或删除失败；5 参数与前置条件不满足。
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
DRY=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        --repo)    REPO="${2:-}"; shift 2 ;;
        --dir)     DIR="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TAG}" ] || { err "缺少 --tag"; exit "${EINVAL}"; }
[ -n "${REPO}" ] || { err "缺少 --repo（或 GITHUB_REPOSITORY）"; exit "${EINVAL}"; }
[ -d "${DIR}" ] || { err "目录不存在: ${DIR}"; exit "${EINVAL}"; }

TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [ -z "${TOKEN}" ] && [ "${DRY}" -eq 0 ]; then
    err "缺少 GITHUB_TOKEN（只读演练请加 --dry-run）"
    exit "${EINVAL}"
fi

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

airy_asset_want "${DIR}" "${work}/want" || exit "${EINVAL}"

airy_asset_have "${REPO}" "${TAG}" "${work}/have" "${work}/rel.json"
rc=$?
case "${rc}" in
    0) ;;
    "${AIRY_ASSETS_ENOREL}") say "Release ${TAG} 尚未建立，无需清理"; exit 0 ;;
    *) exit 1 ;;
esac

kept=0
gone=0
while read -r id name; do
    [ -n "${id:-}" ] || continue
    if grep -Fxq -- "${name}" "${work}/want"; then
        kept=$((kept + 1))
        continue
    fi
    if [ "${DRY}" -eq 1 ]; then
        say "待删除 ${name}"
        gone=$((gone + 1))
        continue
    fi
    code="$(airy_assets_api DELETE "${AIRY_ASSETS_API}/repos/${REPO}/releases/assets/${id}" /dev/null)"
    [ "${code}" = "204" ] || { err "删除 ${name} 失败: HTTP ${code}"; exit 1; }
    say "已删除 ${name}"
    gone=$((gone + 1))
done <"${work}/have"

if [ "${DRY}" -eq 1 ]; then
    say "演练: 本次发布 ${kept} 项，陈旧 ${gone} 项（未改动，${TAG}）"
else
    say "本次发布 ${kept} 项，已清理陈旧 ${gone} 项（${TAG}）"
fi
