#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-rpms.sh — 取发行版内核包（不重建）
#
# 用法: ci/gates/release-rpms.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
#                                [--env-key AIRYMAX_REL_RPMS]
#
# 为什么必须复用而不是就地构建：ISO 载荷里的内核包一旦再编一次，同名包的
# sha256 就与发行 .sha256sum 分叉，装机介质再也无法用发行校验和核验——
# 「双链路收敛」判据随之失效。此处只下载、只校验、只计数。
#
# 退出码: 0 通过；1 缺包/缺校验和/计数不符；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=release-rpms
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

TAG=""
REPO=""
DIR=""
ENV_KEY="AIRYMAX_REL_RPMS"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        --repo)    REPO="${2:-}"; shift 2 ;;
        --dir)     DIR="${2:-}"; shift 2 ;;
        --env-key) ENV_KEY="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/release-rpms.sh --tag vX.Y.Z --repo O/R --dir DIR"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${TAG}" ] && [ -n "${REPO}" ] && [ -n "${DIR}" ] || {
    err "--tag/--repo/--dir 均为必填"; exit "${EINVAL}"; }

need_cmd gh sha256sum grep find
mkdir -p "${DIR}"

ver="${TAG#v}"
sum="${DIR}/agent-linux-${ver}.sha256sum"

group "gh release download ${TAG}"
gh release download "${TAG}" --repo "${REPO}" --dir "${DIR}" --clobber \
    --pattern "airymaxos-kernel*.x86_64.rpm" \
    --pattern "agent-linux-${ver}.sha256sum"
endgroup

[ -f "${sum}" ] || die "${TAG} 未提供 agent-linux-${ver}.sha256sum"

grep -E '  airymaxos-kernel[^ ]*\.x86_64\.rpm$' "${sum}" >"${DIR}/kernel.sha256" || true
[ -s "${DIR}/kernel.sha256" ] || die "${TAG} 的校验和未登记内核包"

( cd "${DIR}" && sha256sum -c kernel.sha256 ) || die "发行内核包摘要不符"

want="$(grep -c . "${DIR}/kernel.sha256")"
have="$(find "${DIR}" -name 'airymaxos-kernel*.x86_64.rpm' | wc -l)"
[ "${have}" -eq "${want}" ] ||
    die "内核包 ${have} 个，校验和登记 ${want} 个"

env_set "${ENV_KEY}" "${DIR}"
say "发行内核包就绪: ${have} 个来自 ${TAG}"
