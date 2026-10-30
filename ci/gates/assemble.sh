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
        # 每段带 UTC 时间戳：容器日志是这条链路唯一的排障证据，而 GitHub
        # 在 job 运行期间不提供日志下载（jobs/<id>/logs 返回 BlobNotFound）。
        # 没有时间戳时，事后只能看到「某一步花了 N 小时」，无法定位到具体段。
        stamp() { printf "[%s] %s\n" "$(date -u +%H:%M:%SZ)" "$*"; }

        # 工具链两段装：先纯离线，再在线。
        #
        # 离线段的前提是 actions/cache 把 /var/cache/dnf 整目录带过来了，里面
        # 既有 keepcache=1 留下的 rpm，也有 repodata。命中时这一段的耗时只与
        # 本地解包有关，与镜像站状态无关——这正是「同一棵树 14 分钟 / 2 小时」
        # 差距的来源（run 40866231339 把 240 分钟预算烧穿时，瓶颈就在在线段）。
        # 缓存冷时 --cacheonly 会在秒级失败，随后落到在线段，行为与旧版一致。
        stamp "工具链: 尝试离线安装"
        if timeout 900 dnf -y install --cacheonly --setopt=keepcache=1 \
            '"${pkgs}"'; then
            stamp "工具链: 离线安装完成"
        else
            stamp "工具链: 缓存不足以离线完成，转在线安装"
            # 在线段沿用旧语义：镜像侧 HTTP/2 抖动会让整事务失败
            # （run 40261523474：gtk3-devel 在 all mirrors tried 后硬退），
            # 事务幂等，故刷元数据后有限重试；三次仍失败即硬退出，不掩盖
            # 真实依赖缺失，也不把网络抖动伪装成组装缺陷。
            for try in 1 2 3; do
                if timeout 3600 dnf -y install --setopt=keepcache=1 \
                    --setopt=timeout=60 --setopt=minrate=1000 \
                    --setopt=retries=6 --setopt=max_parallel_downloads=10 \
                    '"${pkgs}"'; then
                    stamp "工具链: 在线安装完成（第 ${try} 次）"
                    break
                fi
                # 只刷元数据，不删已下好的包：工具链缓存是跨轮复用的凭据，
                # 一次网络抖动就把它清空，等于下一轮从零开始。
                stamp "工具链: 第 ${try} 次在线安装未完成"
                dnf clean metadata >/dev/null 2>&1 || true
                [ "${try}" -lt 3 ] || exit 1
            done
        fi

        stamp "组装: 进入 run-image-ci"
        git config --global --add safe.directory "*"
        distro/ci/run-image-ci.sh "$@"
        stamp "组装: run-image-ci 返回 0"
    ' airy "${args[@]}"
rc=$?
endgroup

if [ "${rc}" -ne 0 ]; then
    err "容器内组装失败（rc=${rc}），产物留存在 ${OUT}"
    exit 1
fi

say "组装完成: ${OUT}"
