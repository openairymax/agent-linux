#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/assemble.sh — 在固定 openEuler 容器内组装 ISO（L1）
#
# 用法: ci/gates/assemble.sh --image IMG --src DIR --out DIR --media DIR
#                            --iso-base NAME [--arch ARCH] [--release-rpms DIR]
#                            [--dnf-cache DIR] [--acceptance]
#
# 约束（来自 docs 70-build-system/03-ci-cd-pipeline.md 与验收发行方案 §3）:
#   * 源码以只读挂载进容器，产物只落 --out，不违反「严禁在源码区内构建」。
#   * 介质以只读挂载，容器内不改媒体本身。
#   * 容器内不装 recommend、不更新基线：包集合由 ci/container/build-packages.txt
#     唯一决定，改工具链等于改这个文件，diff 可 review。
#
# --dnf-cache DIR 把容器的 /var/cache/dnf 挂到宿主目录上：工具链只有 34 个
# 直接依赖，但解析出来的 1273 个包每次都要跨公网取一遍，这一步的耗时完全
# 由镜像站状态决定——同一棵树在 run 40818193756 里是 14 分钟，在
# run 40866231339 里涨到两小时以上，并最终烧掉 240 分钟的 job 预算。
# 缓存目录由调用方（image-build 的 actions/cache）按 build-packages.txt 的
# 摘要做键，命中后 dnf 只做本事务安装，不再发生一次包下载。目录为空或缺省
# 时行为与旧版完全一致，本参数不影响产物字节。
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
        --acceptance)   export ACCEPTANCE=1; shift ;;
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
if [ -n "${DNF_CACHE}" ]; then
    mkdir -p "${DNF_CACHE}"
    mounts+=(-v "${DNF_CACHE}:/var/cache/dnf:rw")
    say "工具链缓存: ${DNF_CACHE}"
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
docker run --rm --privileged \
    -e ACCEPTANCE="$(airy_profile)" \
    -e ISO_BASE="${ISO_BASE}" \
    "${mounts[@]}" \
    -w /src \
    "${IMAGE}" bash -c '
        set -euo pipefail
        # 工具链自上游镜像在线装，单次事务会因镜像侧 HTTP/2 抖动整体失败
        # （run 40261523474：gtk3-devel 在 all mirrors tried 后事务级硬退）。
        # 安装事务幂等，故刷元数据后有限重试；三次仍失败即硬退出，不掩盖真实
        # 依赖缺失，也不把网络抖动伪装成组装缺陷。
        for try in 1 2 3; do
            if dnf -y install --setopt=keepcache=1 --setopt=timeout=60 \
                --setopt=minrate=1000 --setopt=retries=6 \
                --setopt=max_parallel_downloads=10 '"${pkgs}"'; then
                break
            fi
            # 只刷元数据，不删已下好的包：工具链缓存是跨轮复用的凭据，
            # 一次网络抖动就把它清空，等于下一轮从零开始。
            echo "dnf install 第 ${try} 次未完成，刷新元数据后重试" >&2
            dnf clean metadata >/dev/null 2>&1 || true
            [ "${try}" -lt 3 ] || exit 1
        done
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
