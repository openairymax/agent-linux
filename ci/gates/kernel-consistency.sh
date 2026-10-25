#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-consistency.sh — 生成物与真相源的零漂移校验
#
# 用法:
#   ci/gates/kernel-consistency.sh [--kind all|matrix|abi|codegen]
#
# 三条校验共用同一个性质：仓库里有「人工维护的真值」和「由真值生成的
# 产物」，一旦两者不一致，说明有人改了真值没重跑生成器，或改了生成物
# 没回头改真值。它们此前是 ci-kernel.yml 里三条一行的 run，分散在三处，
# 失败时还要翻 YAML 才知道调的是哪个脚本；收敛到一条门禁后，含义只看
# 这一个文件即可。
#
#   matrix   任务书矩阵（OS-STD-TOOL-161）
#   abi      ABI 布局黄金样本（OS-IRON-001）
#   codegen  代码生成零漂移
#
# 退出码: 0 通过；1 有漂移；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
KIND=all

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-consistency.sh [--kind all|matrix|abi|codegen]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --kind) KIND="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

case "${KIND}" in all | matrix | abi | codegen) ;;
*)
    note "--kind 只接受 all|matrix|abi|codegen"
    exit "${EINVAL}"
    ;;
esac

cd "${REPO_ROOT}" || die "无法进入 ${REPO_ROOT}"

run_matrix()
{
    group "matrix: 任务书矩阵一致性（OS-STD-TOOL-161）"
    python3 tools/ci-matrix/ci_matrix.py --check
    local rc=$?
    endgroup
    return "${rc}"
}

run_abi()
{
    group "abi: ABI 布局黄金样本（OS-IRON-001）"
    python3 tools/abi-check/abi_check.py --check
    local rc=$?
    endgroup
    return "${rc}"
}

run_codegen()
{
    group "codegen: 生成物零漂移"
    python3 tools/codegen/run_codegen.py --check
    local rc=$?
    endgroup
    return "${rc}"
}

rc=0
case "${KIND}" in
all)
    run_matrix || rc=1
    run_abi || rc=1
    run_codegen || rc=1
    ;;
matrix) run_matrix || rc=1 ;;
abi) run_abi || rc=1 ;;
codegen) run_codegen || rc=1 ;;
esac

if [ "${rc}" -ne 0 ]; then
    die "一致性校验未通过（kind=${KIND}）"
fi
say "一致性校验通过（kind=${KIND}）"
