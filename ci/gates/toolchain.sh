#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/toolchain.sh — 预热构建容器工具链，并自证缓存足以离线安装
#
# 用法: ci/gates/toolchain.sh --image IMG --cache DIR
#
# 为什么单独成一个 gate：build-packages.txt 的 34 个直接依赖会解析出 1273 个
# 包，全部跨公网取，耗时只由镜像站状态决定——同一棵树在 run 40818193756 是
# 14 分钟，在 run 40866231339 涨到两小时并把 job 预算烧穿。它和 CPU/IO 密集的
# ISO 组装混在同一步时，Actions 只报一个总耗时，既看不出慢在哪一段，也让
# actions/cache 的保存挂在 job 成功态上：job 一旦被取消，缓存永不落盘，下一轮
# 又从零下载，形成自锁环（run 40997227280 的现象）。
#
# 拆开之后本 gate 承担三条职责：
#   * 缓存已完整时走 --cacheonly 直接装完，不产生一次包下载；
#   * 未命中时在线获取，rpm 留在 --cache 里供下一轮与装配段复用；
#   * 收尾用干净容器做 --cacheonly 自证，把「缓存到底够不够用」提前在这里
#     判定。装配段因此可以严格离线：它要么确定能装完，要么在秒级报出
#     「工具链未预热」，而不是打到公网再失败两小时。
#
# 缓存键由调用方按 build-packages.txt 的摘要决定，本 gate 不参与命名：
# 清单不变则包集合不变，键不变，缓存可以跨轮复用。
#
# 退出码: 0 缓存覆盖工具链；1 获取失败或不自洽；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PKGS="${ROOT}/ci/container/build-packages.txt"

IMAGE=""
CACHE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --image)   IMAGE="${2:-}"; shift 2 ;;
        --cache)   CACHE="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/toolchain.sh --image IMG --cache DIR"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${IMAGE}" ] && [ -n "${CACHE}" ] || { err "--image/--cache 均为必填"; exit "${EINVAL}"; }
[ -f "${PKGS}" ] || die "缺少工具链清单: ${PKGS}"
need_cmd docker

pkgs="$(grep -vE '^[[:space:]]*(#|$)' "${PKGS}" | tr '\n' ' ')"
[ -n "${pkgs}" ] || die "工具链清单为空: ${PKGS}"

mkdir -p "${CACHE}"

# 容器内只做一件事：按模式装同一份包清单。两段共用一份脚本，避免离线与
# 在线两条路径的选项漂移——它们装的必须是同一批包，否则自证不成立。
read -r -d '' script <<'EOF'
set -euo pipefail
stamp() { printf "[%s] %s\n" "$(date -u +%H:%M:%SZ)" "$*"; }
opts=(--setopt=keepcache=1 --setopt=timeout=60 --setopt=minrate=1000
      --setopt=retries=6 --setopt=max_parallel_downloads=10)
if [ "${AIRYMAX_MODE}" = offline ]; then
    opts+=(--cacheonly)
fi
stamp "工具链: ${AIRYMAX_MODE} 安装"
dnf -y install "${opts[@]}" ${AIRYMAX_PKGS}
stamp "工具链: ${AIRYMAX_MODE} 安装完成"
EOF

# 上游仓库定义以只读方式挂进容器。工具链里含 ukui-control-center 重建所需的
# 一组 BuildRequires，它们只在 EPOL；容器镜像自带的 repo 文件不含 EPOL，
# 不挂这一份就会在解析阶段报「没有可用软件包」，而配方本身没有问题。
# 挂的是 distro/repo/airymax-os.repo——目标机 dnf 客户端配置的同一份源文件，
# 构建期与装机后共用一张仓库表。装配段的离线重放必须挂同一份文件，否则
# 缓存里的 EPOL 元数据找不到对应的 repo id，--cacheonly 解析失败。
repo_mount=(-v "${ROOT}/distro/repo/airymax-os.repo"
            ":/etc/yum.repos.d/99-airy-upstream.repo:ro")

# timeout(1) 只执行外部命令，包在 shell 函数上会退成
# 「timeout: failed to run command 'dnf_run'」（run 41613120035）。时限因此
# 加在 docker 这一层：命令先装进数组，需要限时才在数组头部插 timeout。
# 第一参数是秒数，0 表示不限；第二参数是在线/离线模式。
dnf_run()
{
    local limit="$1" mode="$2"
    local cmd=(docker run --rm -v "${CACHE}:/var/cache/dnf:rw"
               "${repo_mount[@]}")

    if [ "${limit}" -gt 0 ]; then
        cmd=(timeout "${limit}" "${cmd[@]}")
    fi
    "${cmd[@]}" -e AIRYMAX_PKGS="${pkgs}" -e AIRYMAX_MODE="${mode}" \
        "${IMAGE}" bash -c "${script}"
}

group "toolchain offline attempt"
if dnf_run 0 offline; then
    say "缓存已完整，跳过在线获取"
else
    # 在线段沿用旧语义里的有限重试：镜像侧 HTTP/2 抖动会让整事务失败
    # （run 40261523474：gtk3-devel 在 all mirrors tried 后硬退）。只刷元
    # 数据、不删已下好的包——缓存是跨轮复用的凭据，一次抖动清空它等于
    # 下一轮从零开始。
    for try in 1 2 3; do
        if dnf_run 3600 online; then
            say "在线获取完成（第 ${try} 次）"
            break
        fi
        warn "第 ${try} 次在线获取未完成"
        docker run --rm -v "${CACHE}:/var/cache/dnf:rw" \
            "${repo_mount[@]}" "${IMAGE}" \
            dnf clean metadata >/dev/null 2>&1 || true
        [ "${try}" -lt 3 ] || die "工具链在线获取三次未成功"
    done
    group "toolchain offline proof"
    dnf_run 0 offline || die "缓存不足以离线完成工具链安装，装配段不可用"
    endgroup
fi
endgroup

say "工具链缓存: ${CACHE} ($(du -sh "${CACHE}" | cut -f1))"
