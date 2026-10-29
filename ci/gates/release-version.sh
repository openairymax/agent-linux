#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-version.sh — 发布 tag 归一化与合法性校验
#
# 用法: ci/ci.sh release-version [--tag TAG]
#
# 发布链上的版本号只允许一个来源：tag 事件取 github.ref_name，手动触发取
# inputs.tag。归一化（补 v 前缀）与格式校验收在这里，后续每一步只读
# tag/ver 两个输出，不再各自解析一遍。
#
# 非法 tag 必须在最便宜的一步失败：它一旦漏过去，四架构内核构建会按一个
# 不存在的版本号产包，签名与 Release 也随之指向错误版本。
#
# 输出（$GITHUB_OUTPUT）: tag=<vX.Y.Z> ver=<X.Y.Z>；本地运行时打印到 stderr。
#
# 退出码: 0 通过；1 tag 非法；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$'
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

TAG="${RELEASE_TAG:-}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/ci.sh release-version [--tag TAG]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${TAG}" ] || { err "缺少发布 tag（--tag 或 RELEASE_TAG）"; exit "${EINVAL}"; }

case "${TAG}" in v*) ;; *) TAG="v${TAG}" ;; esac
ver="${TAG#v}"

printf '%s' "${ver}" | grep -Eq "${VERSION_RE}" ||
    die "非法的发布 tag: ${TAG}（应为 vX.Y.Z 或 vX.Y.Z-rcN）"

out_set tag "${TAG}"
out_set ver "${ver}"
say "发布版本 ${TAG}（ver=${ver}）"
