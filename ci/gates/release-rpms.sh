#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-rpms.sh — 取发行版内核包（不重建）
#
# 用法: ci/gates/release-rpms.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
#                                [--arch x86_64] [--env-key AIRYMAX_REL_RPMS]
#                                [--run-id N]
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
ARCH="x86_64"
ENV_KEY="AIRYMAX_REL_RPMS"
RUN_ID=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        --repo)    REPO="${2:-}"; shift 2 ;;
        --dir)     DIR="${2:-}"; shift 2 ;;
        --arch)    ARCH="${2:-}"; shift 2 ;;
        --env-key) ENV_KEY="${2:-}"; shift 2 ;;
        --run-id)  RUN_ID="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/release-rpms.sh --tag vX.Y.Z --repo O/R --dir DIR" \
                 "[--arch ARCH] [--run-id N]"
            exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TAG}" ] && [ -n "${REPO}" ] && [ -n "${DIR}" ] || {
    err "--tag/--repo/--dir 均为必填"; exit "${EINVAL}"; }
case "${ARCH}" in
    ''|*[!a-z0-9_]*) err "--arch 必须是 RPM 架构名（如 x86_64/aarch64）: ${ARCH}"; exit "${EINVAL}" ;;
esac
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
    gh run download "${RUN_ID}" --repo "${REPO}" \
        --name "release-artifacts-${ver}" --dir "${DIR}" --clobber ||
        die "run ${RUN_ID} 无 release-artifacts-${ver} 制品"
    endgroup
else
    group "gh release download ${TAG}"
    gh release download "${TAG}" --repo "${REPO}" --dir "${DIR}" --clobber \
        --pattern "airymaxos-kernel*.${ARCH}.rpm" \
        --pattern "agent-linux-${ver}.sha256sum"
    endgroup
fi

[ -f "${sum}" ] || die "${TAG} 未提供 agent-linux-${ver}.sha256sum"

grep -E "  airymaxos-kernel[^ ]*\.${ARCH}\.rpm$" "${sum}" >"${DIR}/kernel.sha256" || true
[ -s "${DIR}/kernel.sha256" ] || die "${TAG} 的校验和未登记 ${ARCH} 内核包"

# 校验和里只写文件名，而制品保留了 dist/rpm/<arch>/ 的目录层级（发布页下载
# 是平铺的）。先按清单里的首个文件名反查它落盘的目录，再在该目录里整批
# 复验：两种取件路径因此共用同一份清单，不各自假定布局。
first="$(awk 'NR==1{print $2}' "${DIR}/kernel.sha256")"
probe="$(find "${DIR}" -name "${first#\*}" -print -quit)"
[ -n "${probe}" ] || die "${TAG} 制品内找不到 ${first#\*}"
rpmdir="$(dirname "${probe}")"

( cd "${rpmdir}" && sha256sum -c "${DIR}/kernel.sha256" ) ||
    die "发行内核包摘要不符"

want="$(grep -c . "${DIR}/kernel.sha256")"
have="$(find "${rpmdir}" -name "airymaxos-kernel*.${ARCH}.rpm" | wc -l)"
[ "${have}" -eq "${want}" ] ||
    die "内核包 ${have} 个，校验和登记 ${want} 个"

env_set "${ENV_KEY}" "${rpmdir}"
say "发行内核包就绪: ${have} 个来自 ${TAG}"
