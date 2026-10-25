#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/assemble.sh — 在固定 openEuler 容器内组装 ISO（L1）
#
# 用法: ci/gates/assemble.sh --image IMG --src DIR --out DIR --media DIR
#                            --iso-base NAME [--arch ARCH] [--release-rpms DIR]
#                            [--acceptance]
#
# 约束（来自 docs 70-build-system/03-ci-cd-pipeline.md 与验收发行方案 §3）:
#   * 源码以只读挂载进容器，产物只落 --out，不违反「严禁在源码区内构建」。
#   * 介质以只读挂载，容器内不改媒体本身。
#   * 容器内不装 recommend、不更新基线：包集合由 ci/container/build-packages.txt
#     唯一决定，改工具链等于改这个文件，diff 可 review。
#
# 这个 gate 的日志是排障的主证据：容器内的 run-image-ci.sh 会把每段产物写进
# --out，失败时由 ci/gates/log-dump.sh 统一摊开，所以这里不吞输出。
#
# 退出码: 0 通过；1 容器内组装失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PKGS="${ROOT}/ci/container/build-packages.txt"

IMAGE=""
SRC=""
OUT=""
MEDIA=""
ISO_BASE=""
ARCH="x86_64"
REL_RPMS="${AIRYMAX_REL_RPMS:-}"
ACCEPTANCE=0

# 剖面走环境变量，与 accept/tap-extract 同一约定。
case "${ACCEPTANCE:-0}" in
    1|true|yes) ACCEPTANCE=1 ;;
    *)          ACCEPTANCE=0 ;;
esac

while [ "$#" -gt 0 ]; do
    case "$1" in
        --image)        IMAGE="${2:-}"; shift 2 ;;
        --src)          SRC="${2:-}"; shift 2 ;;
        --out)          OUT="${2:-}"; shift 2 ;;
        --media)        MEDIA="${2:-}"; shift 2 ;;
        --iso-base)     ISO_BASE="${2:-}"; shift 2 ;;
        --arch)         ARCH="${2:-}"; shift 2 ;;
        --release-rpms) REL_RPMS="${2:-}"; shift 2 ;;
        --acceptance)   ACCEPTANCE=1; shift ;;
        -h|--help)
            echo "用法: ci/gates/assemble.sh --image IMG --src DIR --out DIR --media DIR --iso-base NAME"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${IMAGE}" ] && [ -n "${SRC}" ] && [ -n "${OUT}" ] && \
    [ -n "${MEDIA}" ] && [ -n "${ISO_BASE}" ] || {
    err "--image/--src/--out/--media/--iso-base 均为必填"; exit "${EINVAL}"; }

[ -d "${SRC}" ] || die "源码目录不存在: ${SRC}"
[ -d "${MEDIA}" ] || die "介质目录不存在: ${MEDIA}"
[ -f "${MEDIA}/${ISO_BASE}" ] || die "基础 ISO 不存在: ${MEDIA}/${ISO_BASE}"
[ -f "${PKGS}" ] || die "缺少工具链清单: ${PKGS}"
need_cmd docker

pkgs="$(grep -vE '^[[:space:]]*(#|$)' "${PKGS}" | tr '\n' ' ')"
[ -n "${pkgs}" ] || die "工具链清单为空: ${PKGS}"

mkdir -p "${OUT}"

mounts=(-v "${SRC}:/src:ro" -v "${OUT}:/out" -v "${MEDIA}:/media:ro")
args=(--out /out --iso-base "/media/${ISO_BASE}"
      --profile desktop --arch "${ARCH}" --skip-install)
if [ -n "${REL_RPMS}" ]; then
    mounts+=(-v "${REL_RPMS}:/rel-rpms:ro")
    args+=(--kernel-rpms /rel-rpms)
fi
if [ "${ACCEPTANCE}" -eq 1 ]; then
    args+=(--acceptance)
fi

group "assemble in ${IMAGE}"
docker run --rm --privileged \
    -e ACCEPTANCE="${ACCEPTANCE}" \
    -e ISO_BASE="${ISO_BASE}" \
    "${mounts[@]}" \
    -w /src \
    "${IMAGE}" bash -c '
        set -euo pipefail
        dnf -y install --setopt=timeout=30 --setopt=minrate=1000 \
            --setopt=retries=5 --setopt=max_parallel_downloads=10 '"${pkgs}"'
        git config --global --add safe.directory "*"
        distro/ci/run-image-ci.sh "$@"
    ' airy "${args[@]}"
rc=$?
endgroup

if [ "${rc}" -ne 0 ]; then
    err "容器内组装失败（rc=${rc}），产物留存在 ${OUT}"
    exit 1
fi

say "组装完成: ${OUT}"
