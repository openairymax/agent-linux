#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-sbom.sh — 生成并合并 SPDX 物料清单
#
# 用法: ci/ci.sh release-sbom --ver VER [--namespace URL]
#
# 扫描内核子仓与 SBOM 方案 §2.4 登记的用户态子仓，逐仓产出 SPDX，再合并成
# 一份对外物料清单。分仓扫描而不是整树扫描：发布页声明的范围就是这 8 个仓，
# CI 脚本与文档不在其中，整树扫描会把构建机的临时产物也算进物料。
#
# 合并走 tools/release-engineering/merge_sbom.py（真实合并器，非拼接）。
#
# 退出码: 0 通过；1 扫描或合并失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly REPOS=(memory security cognition services system cloudnative tests-linux)
readonly NAMESPACE_BASE="https://airymaxos.com/spdx"

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

VER=""
NAMESPACE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ver)       VER="${2:-}"; shift 2 ;;
        --namespace) NAMESPACE="${2:-}"; shift 2 ;;
        -h|--help)   echo "用法: ci/ci.sh release-sbom --ver VER [--namespace URL]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
NAMESPACE="${NAMESPACE:-${NAMESPACE_BASE}/agent-linux-${VER}}"
need_cmd syft python3

parts=()
group "sbom: kernel"
syft kernel -o spdx-json=sbom-kernel.spdx.json || die "内核子仓扫描失败"
parts+=(sbom-kernel.spdx.json)
endgroup

for repo in "${REPOS[@]}"; do
    group "sbom: ${repo}"
    syft "${repo}" -o spdx-json="sbom-${repo}.spdx.json" ||
        die "子仓扫描失败: ${repo}"
    parts+=("sbom-${repo}.spdx.json")
    endgroup
done

python3 tools/release-engineering/merge_sbom.py \
    --name "agent-linux-${VER}" \
    --namespace "${NAMESPACE}" \
    --output "sbom-agent-linux-${VER}.spdx.json" \
    "${parts[@]}" || die "SBOM 合并失败"

say "SBOM 已合并: sbom-agent-linux-${VER}.spdx.json（${#parts[@]} 个来源）"
