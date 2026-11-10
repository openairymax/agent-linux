#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-rpms.sh — 取发行版内核包（不重建）
#
# 用法: ci/gates/release-rpms.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
#                                [--arch x86_64]... [--env-key AIRYMAX_REL_RPMS]
#                                [--run-id N]
#
# --arch 可重复：一次取件只下载一份制品，却可以按多个架构各复核一遍。装配
# 段只要目标架构的包，跨架构预演（ci/gates/fw-preflight.sh）要的是每个架构
# 的模块树——它问的是「该架构模块请求的固件，固件树里有没有」，只有四个
# 架构的模块包同时在场才答得出。分成两次取件会重复下载同一份制品，故把
# 「取一次、验多份」做成一个入口；计数器与摘要逐架构独立，一个架构缺登记
# 仍然判负，不会因为别的架构齐全而放过。
#
# --run-id 取发布流水线 build-and-sign 作业的 release-artifacts-<ver> 制品，
# 而不是发布页。两者字节相同（同一作业先上传制品再签名发布），差别只在
# 时序：首个版本的发布页要等通道清单，通道清单要等装配出 ISO 的那一轮，
# 而那一轮又必须复用发行内核包——三条判据串成环，谁先谁后都无解。制品
# 侧的取件口把「内核包从哪来」与「发布页何时建」解耦，环由时序消失，
# 判据一条不减。
#
# --arch 必填语义、可省写法：取件方清楚本轮介质的目标架构（ISO 基线就是
# x86_64），门禁不替它猜。此前把 x86_64 写进 glob，架构这一事实藏在门禁
# 内部，调用点看不出取的是哪一架构的包，换架构时也不会有人想起改这里。
#
# 为什么必须复用而不是就地构建：ISO 载荷里的内核包一旦再编一次，同名包的
# sha256 就与发行 .sha256sum 分叉，装机介质再也无法用发行校验和核验——
# 「双链路收敛」判据随之失效。此处只下载、只校验、只计数。
#
# 退出码: 0 通过；1 缺包/缺校验和/计数不符；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

TAG=""
REPO=""
DIR=""
ARCHS=()
ENV_KEY="AIRYMAX_REL_RPMS"
RUN_ID=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        --repo)    REPO="${2:-}"; shift 2 ;;
        --dir)     DIR="${2:-}"; shift 2 ;;
        --arch)    ARCHS+=("${2:-}"); shift 2 ;;
        --env-key) ENV_KEY="${2:-}"; shift 2 ;;
        --run-id)  RUN_ID="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/release-rpms.sh --tag vX.Y.Z --repo O/R --dir DIR" \
                 "[--arch ARCH]... [--run-id N]"
            exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TAG}" ] && [ -n "${REPO}" ] && [ -n "${DIR}" ] || {
    err "--tag/--repo/--dir 均为必填"; exit "${EINVAL}"; }
# 缺省 x86_64 是装配段的默认目标，不是本门禁的猜测：ISO 基线就固定为
# x86_64，调用方不写 --arch 时按它取件。预演段必须显式列出它要的每个架构。
[ "${#ARCHS[@]}" -gt 0 ] || ARCHS=(x86_64)
for a in "${ARCHS[@]}"; do
    case "${a}" in
        ''|*[!a-z0-9_]*) err "--arch 必须是 RPM 架构名（如 x86_64/aarch64）: ${a}"; exit "${EINVAL}" ;;
    esac
done
case "${RUN_ID}" in
    '') ;;
    *[!0-9]*) err "--run-id 必须是数字: ${RUN_ID}"; exit "${EINVAL}" ;;
esac

need_cmd gh sha256sum grep find awk
mkdir -p "${DIR}"

ver="${TAG#v}"
sum="${DIR}/agent-linux-${ver}.sha256sum"

if [ -n "${RUN_ID}" ]; then
    group "gh run download ${RUN_ID}"
    # gh run download 没有 --clobber（那是 gh release download 的开关）；
    # 传了会让整步死在参数解析上，报文却写成「无制品」，把 CLI 用法错误伪装
    # 成资产缺失。目标目录由调用方给出且为空，覆盖语义本就不需要。
    gh run download "${RUN_ID}" --repo "${REPO}" \
        --name "release-artifacts-${ver}" --dir "${DIR}" ||
        die "run ${RUN_ID} 无 release-artifacts-${ver} 制品"
    endgroup
else
    group "gh release download ${TAG}"
    pat=()
    for a in "${ARCHS[@]}"; do
        pat+=(--pattern "airymaxos-kernel*.${a}.rpm")
    done
    gh release download "${TAG}" --repo "${REPO}" --dir "${DIR}" --clobber \
        "${pat[@]}" --pattern "agent-linux-${ver}.sha256sum"
    endgroup
fi

[ -f "${sum}" ] || die "${TAG} 未提供 agent-linux-${ver}.sha256sum"

# rel_arch ARCH —— 复核单一架构的内核包，成功时把它的落盘目录记进 REL_DIR。
# 摘要、计数、目录定位三项对一个架构分别成立才算过：把它们合成一次全量
# 比对，缺件会被别的架构的齐全掩盖，而预演要的恰是「每个架构都在场」。
REL_DIR=""
rel_arch()
{
    local a="$1" list first probe dir want have

    list="${DIR}/kernel-${a}.sha256"
    grep -E "  airymaxos-kernel[^ ]*\.${a}\.rpm$" "${sum}" >"${list}" || true
    [ -s "${list}" ] || die "${TAG} 的校验和未登记 ${a} 内核包"

    # 校验和里只写文件名，而制品保留了 dist/rpm/<arch>/ 的目录层级（发布页
    # 下载是平铺的）。先按清单里的首个文件名反查它落盘的目录，再在该目录里
    # 整批复验：两种取件路径因此共用同一份清单，不各自假定布局。
    first="$(awk 'NR==1{print $2}' "${list}")"
    probe="$(find "${DIR}" -name "${first#\*}" -print -quit)"
    [ -n "${probe}" ] || die "${TAG} 制品内找不到 ${first#\*}"
    dir="$(dirname "${probe}")"

    ( cd "${dir}" && sha256sum -c "${list}" ) ||
        die "${a} 发行内核包摘要不符"

    want="$(grep -c . "${list}")"
    have="$(find "${dir}" -name "airymaxos-kernel*.${a}.rpm" | wc -l)"
    [ "${have}" -eq "${want}" ] ||
        die "${a} 内核包 ${have} 个，校验和登记 ${want} 个"

    REL_DIR="${dir}"
    say "发行内核包就绪: ${have} 个 ${a} 来自 ${TAG}"
}

# 出口只指向第一个架构的目录：装配段按单架构取件（ISO 基线 x86_64），
# 跨架构预演则直接扫 --rpms 根目录下的全部架构子目录，不依赖这个出口。
RET_DIR=""
for a in "${ARCHS[@]}"; do
    rel_arch "${a}"
    [ -n "${RET_DIR}" ] || RET_DIR="${REL_DIR}"
done

# 取件方要的是「发行资产在哪」，不止「内核包在哪」。制品路径把 RPM 留在
# dist/rpm/<arch>/ 里，摘要清单留在制品根；发布页路径两者平铺。若只交出
# RPM 目录，下游就得自己在里面按通配符找清单——平铺时碰巧找到，制品路径
# 必然找不到，而失败消息会写成「发行目录没有校验和」，把布局差异伪装成
# 资产缺失。布局由本门禁唯一掌握，两个出口一起给出，下游不再猜。
env_set "${ENV_KEY}" "${RET_DIR}"
env_set AIRYMAX_REL_SUM "${sum}"
