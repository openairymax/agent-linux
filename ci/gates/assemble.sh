#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/assemble.sh — 在固定 openEuler 容器内组装 ISO（L1）
#
# 用法: ci/gates/assemble.sh --image IMG --src DIR --out DIR --media DIR
#                            --iso-base NAME --dnf-cache DIR
#                            [--closure-cache DIR] [--arch ARCH]
#                            [--release-rpms DIR] [--acceptance]
#
# 约束（来自 docs 70-build-system/03-ci-cd-pipeline.md 与验收发行方案 §3）:
#   * 源码以只读挂载进容器，产物只落 --out，不违反「严禁在源码区内构建」。
#   * 介质以只读挂载，容器内不改媒体本身。
#   * 容器内不装 recommend、不更新基线：包集合由 ci/container/build-packages.txt
#     唯一决定，改工具链等于改这个文件，diff 可 review。
#
# 工具链不在本 gate 获取：ci/gates/toolchain.sh 已经把它装进 --dnf-cache 并
# 用干净容器自证过可离线安装。这里只做 --cacheonly 的严格重放——装不上就
# 说明缓存没预热，秒级失败比打公网两小时再烧穿预算有价值（run 40997227280
# 就是两者混在一步、缓存又挂在 job 成功态上，取消即丢、下轮从零开始）。
# 因此 --dnf-cache 是必填参数：没有它就不存在「可离线重放」的前提。
#
# --closure-cache 是可选第二块缓存：安装闭包（约 2.5 GB RPM）的跨轮复用池。
# 它不参与工具链重放，只由 distro/scripts/lib/closure.sh 以 cost=1 的本地
# 仓库参与求解。缺省时不挂载，行为与不带此参数的历史版本完全一致。
#
# 这个 gate 的日志是排障的主证据：容器内的 run-image-ci.sh 会把每段产物写进
# --out，失败时由 ci/gates/log-dump.sh 统一摊开，所以这里不吞输出。
#
# 退出码: 0 通过；1 容器内组装失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"
# shellcheck source=../lib/profile.sh
. "${LIB}/profile.sh"
# shellcheck source=../lib/progress.sh
. "${LIB}/progress.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PKGS="${ROOT}/ci/container/build-packages.txt"

IMAGE=""
SRC=""
OUT=""
MEDIA=""
ISO_BASE=""
ARCH="x86_64"
REL_RPMS="${AIRYMAX_REL_RPMS:-}"
DNF_CACHE=""
CLOSURE_CACHE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --image)        IMAGE="${2:-}"; shift 2 ;;
        --src)          SRC="${2:-}"; shift 2 ;;
        --out)          OUT="${2:-}"; shift 2 ;;
        --media)        MEDIA="${2:-}"; shift 2 ;;
        --iso-base)     ISO_BASE="${2:-}"; shift 2 ;;
        --arch)         ARCH="${2:-}"; shift 2 ;;
        --release-rpms) REL_RPMS="${2:-}"; shift 2 ;;
        --dnf-cache)    DNF_CACHE="${2:-}"; shift 2 ;;
        --closure-cache) CLOSURE_CACHE="${2:-}"; shift 2 ;;
        --acceptance)   export ACCEPTANCE=1; shift ;;
        -h|--help)
            echo "用法: ci/gates/assemble.sh --image IMG --src DIR --out DIR --media DIR --iso-base NAME --dnf-cache DIR [--closure-cache DIR]"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${IMAGE}" ] && [ -n "${SRC}" ] && [ -n "${OUT}" ] && \
    [ -n "${MEDIA}" ] && [ -n "${ISO_BASE}" ] && [ -n "${DNF_CACHE}" ] || {
    err "--image/--src/--out/--media/--iso-base/--dnf-cache 均为必填"; exit "${EINVAL}"; }

[ -d "${SRC}" ] || die "源码目录不存在: ${SRC}"
[ -d "${MEDIA}" ] || die "介质目录不存在: ${MEDIA}"
[ -f "${MEDIA}/${ISO_BASE}" ] || die "基础 ISO 不存在: ${MEDIA}/${ISO_BASE}"
[ -f "${PKGS}" ] || die "缺少工具链清单: ${PKGS}"
[ -d "${DNF_CACHE}" ] || die "工具链缓存不存在，先跑 ci/ci.sh toolchain: ${DNF_CACHE}"
need_cmd docker

pkgs="$(grep -vE '^[[:space:]]*(#|$)' "${PKGS}" | tr '\n' ' ')"
[ -n "${pkgs}" ] || die "工具链清单为空: ${PKGS}"

mkdir -p "${OUT}"
# 缓存池目录由 runner 先建：不存在的宿主路径会被 docker 建成 root:root，
# 后续 actions/cache/save 以 runner 身份读它时行为不可依赖。
[ -n "${CLOSURE_CACHE}" ] && mkdir -p "${CLOSURE_CACHE}"

mounts=(-v "${SRC}:/src:ro" -v "${OUT}:/out" -v "${MEDIA}:/media:ro"
        -v "${DNF_CACHE}:/var/cache/dnf:rw")
envs=(-e ACCEPTANCE="$(airy_profile)" -e ISO_BASE="${ISO_BASE}")
# 闭包缓存池是可选的：没有它照样能组装，只是每一轮都要把 2.5 GB 闭包
# 从公网重取一遍（run 41023530128 因此撞上 step 时限）。有它就把池挂进
# 容器，closure.sh 以 cost=1 的本地仓库参与求解，远端只补差异。
if [ -n "${CLOSURE_CACHE}" ]; then
    mounts+=(-v "${CLOSURE_CACHE}:/var/cache/airymax-closure:rw")
    envs+=(-e CLOSURE_POOL=/var/cache/airymax-closure)
fi
args=(--out /out --iso-base "/media/${ISO_BASE}"
      --profile desktop --arch "${ARCH}" --skip-install)
if [ -n "${REL_RPMS}" ]; then
    mounts+=(-v "${REL_RPMS}:/rel-rpms:ro")
    args+=(--kernel-rpms /rel-rpms)
fi
if [ "$(airy_profile)" -eq 1 ]; then
    args+=(--acceptance)
fi

group "assemble in ${IMAGE}"
say "工具链缓存: ${DNF_CACHE}"
# 组装段 40 分钟起步，且内核模块数与闭包拉取都由远端决定，静默与挂死在
# 控制台上同形。心跳只读 ${OUT} 下最新日志的元数据，不干扰容器。
progress_start "${OUT}"
trap progress_stop EXIT
docker run --rm --privileged \
    "${envs[@]}" \
    "${mounts[@]}" \
    -w /src \
    "${IMAGE}" bash -c '
        set -euo pipefail
        # 每段带 UTC 时间戳：容器日志是这条链路唯一的排障证据，而 GitHub
        # 在 job 运行期间不提供日志下载（jobs/<id>/logs 返回 BlobNotFound）。
        # 没有时间戳时，事后只能看到「某一步花了 N 小时」，无法定位到具体段。
        stamp() { printf "[%s] %s\n" "$(date -u +%H:%M:%SZ)" "$*"; }

        # 严格离线：缓存由 ci/gates/toolchain.sh 预热并自证过。这里装不上
        # 只有一种含义——缓存没预热，秒级失败，不回落到公网。
        stamp "工具链: 离线重放"
        dnf -y install --cacheonly --setopt=keepcache=1 \
            --setopt=timeout=60 '"${pkgs}"' || {
            echo "工具链未预热：先运行 ci/ci.sh toolchain --cache <DIR>" >&2
            exit 1
        }
        stamp "工具链: 离线重放完成"

        stamp "组装: 进入 run-image-ci"
        git config --global --add safe.directory "*"
        distro/ci/run-image-ci.sh "$@"
        stamp "组装: run-image-ci 返回 0"
    ' airy "${args[@]}"
rc=$?
progress_stop
endgroup

if [ "${rc}" -ne 0 ]; then
    err "容器内组装失败（rc=${rc}），产物留存在 ${OUT}"
    exit 1
fi

say "组装完成: ${OUT}"
