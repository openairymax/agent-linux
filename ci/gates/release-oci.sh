#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-oci.sh — 基础 OCI 镜像的确定性构建与发布
#
# 用法: ci/ci.sh release-oci build   --tag TAG --ver VER
#       ci/ci.sh release-oci publish --tag TAG
#
# build 在发布 job 内完成镜像组装并落成 tar，publish 在第二台 runner 上
# 重新载入并推送：两段的中间产物就是发布页挂出的 oci-image-*.tar，摘要
# 可被取件方独立复核。
#
# SOURCE_DATE_EPOCH 缺省取末次提交时间（§3.7），保证同一 tag 重跑得到同一
# 层摘要；未显式设置时由本 gate 补齐，避免调用方漏传后悄悄产出不可复现镜像。
#
# cosign 必须在 push 之后签名：签名对象是已推送镜像的 digest，先签本地
# tag 等于签了一个尚未存在的对象。
#
# 退出码: 0 通过；1 构建/推送/签名失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
OCI_IMAGE="${OCI_IMAGE:-ghcr.io/openairymax/airymaxos}"

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

cmd="${1:-}"
[ "$#" -gt 0 ] || { echo "用法: ci/ci.sh release-oci <build|publish> [选项]" >&2; exit "${EINVAL}"; }
shift

TAG=""
VER=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        --ver)     VER="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/ci.sh release-oci <build|publish> --tag TAG [--ver VER]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${TAG}" ] || { err "缺少 --tag"; exit "${EINVAL}"; }

build()
{
    local epoch

    need_cmd docker
    [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
    epoch="${SOURCE_DATE_EPOCH:-$(git log -1 --pretty=%ct)}"
    docker build -f oci/Containerfile.agent-base \
        --build-arg "SOURCE_DATE_EPOCH=${epoch}" \
        --build-arg "AIRY_VERSION=${VER}" \
        --build-arg "RPM_ARCH=x86_64" \
        --build-arg "TZ=UTC" --build-arg "LC_ALL=C" \
        -t "${OCI_IMAGE}:${TAG}" . || die "镜像构建失败"
    docker save "${OCI_IMAGE}:${TAG}" -o "oci-image-${TAG}.tar" ||
        die "镜像导出失败"
    say "已导出 oci-image-${TAG}.tar"
}

publish()
{
    [ -n "${GITHUB_TOKEN:-}" ] || { err "缺少 GITHUB_TOKEN"; exit "${EINVAL}"; }
    need_cmd docker cosign
    printf '%s' "${GITHUB_TOKEN}" | docker login ghcr.io \
        -u "${GITHUB_ACTOR:-github-actions}" --password-stdin ||
        die "ghcr 登录失败"
    docker load -i "oci-image-${TAG}.tar" || die "镜像载入失败"
    docker push "${OCI_IMAGE}:${TAG}" || die "镜像推送失败"
    cosign sign --yes --key env://COSIGN_KEY "${OCI_IMAGE}:${TAG}" ||
        die "cosign 签名失败"
    say "已发布 ${OCI_IMAGE}:${TAG}"
}

case "${cmd}" in
    build)   build ;;
    publish) publish ;;
    -h|--help) echo "用法: ci/ci.sh release-oci <build|publish> --tag TAG [--ver VER]" ;;
    *) echo "用法: ci/ci.sh release-oci <build|publish> [选项]" >&2; exit "${EINVAL}" ;;
esac
