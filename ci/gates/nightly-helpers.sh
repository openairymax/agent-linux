#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/nightly-helpers.sh — nightly 的 deferred helper 调度器
#
# 用法: ci/ci.sh nightly-helpers <helper> [参数...]
#
# nightly 的六个 helper（formal-verify / soak-runner / chaos-runner /
# auto-bisect / create-revert-pr / ci-budget-check）尚未落地。它们此前以
# 「if [ -f <path> ] … else echo SKIP」的形式内联在 workflow 里：守卫逻辑
# 与调用参数只能靠云端 job 验证，六段内联块也无法在本地重放一遍。
#
# 本门禁把守卫与参数收在一处：workflow 只剩 `ci/ci.sh nightly-helpers <key>`，
# helper 是否落地、用什么参数调用、未落地时打印什么标记，全部由这里决定。
# 契约（路径 / argv / deferred 状态）仍以 tools/nightly/manifest.yaml 为准，
# tools/nightly/nightly.py 读取本文件做交叉校验。
#
# 退出码: 0 通过或按契约跳过；1 helper 执行失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/ci.sh nightly-helpers <helper> [参数...]

  formal-verify      结构化验证：kernel/sched/core.c、airy_ipc.c、share_pool.c
  soak-runner        72h 长稳（内存泄漏 / 引用计数漂移）
  chaos-runner       故障注入（热插拔 / 热移除 / I/O / 网络分区）
  auto-bisect        失败后二分回归窗口（--baseline last-green）
  create-revert-pr   对回归提交开回滚 PR（参数：<commit>）
  ci-budget-check    CI 总时长预算（OS-STD-TEST-011，--max-minutes 60）
EOF
}

# helper 未落地时统一走这里：标记必须与清单登记的 deferred 状态一致，
# 否则 nightly.py 会判定契约漂移。
skip()
{
    echo "SKIP: $1 not yet implemented"
}

formal_verify()
{
    local target
    if [ -f tools/formal-verify.py ]; then
        for target in kernel/sched/core.c \
                      drivers/airymax/ipc/airy_ipc.c \
                      mm/share_pool.c; do
            python3 tools/formal-verify.py --target "${target}" || return 1
        done
    else
        skip formal-verify.py
    fi
}

# 72h soak 需要 self-hosted runner（托管 runner 单 job 上限 6 小时），
# soak-runner.py 落地时须同步调整 runner 与清单。
soak_runner()
{
    if [ -f tools/soak-runner.py ]; then
        python3 tools/soak-runner.py --duration 72h --workload agent-busy-loop
    else
        skip soak-runner.py
    fi
}

chaos_runner()
{
    if [ -f tools/chaos-runner.py ]; then
        python3 tools/chaos-runner.py --profile full
    else
        skip chaos-runner.py
    fi
}

auto_bisect()
{
    if [ -f tools/auto-bisect.py ]; then
        python3 tools/auto-bisect.py --baseline last-green
    else
        skip auto-bisect.py
    fi
}

create_revert_pr()
{
    local commit="${1:-}"
    if [ -z "${commit}" ]; then
        err "create-revert-pr 需要回归提交号"
        return "${EINVAL}"
    fi
    if [ -f tools/create-revert-pr.py ]; then
        python3 tools/create-revert-pr.py --regression-commit "${commit}"
    else
        skip create-revert-pr.py
    fi
}

budget_check()
{
    if [ -f tools/ci-budget-check.py ]; then
        python3 tools/ci-budget-check.py --max-minutes 60
    else
        skip ci-budget-check.py
    fi
}

main()
{
    local key="${1:-}"
    [ "$#" -gt 0 ] && shift
    case "${key}" in
        formal-verify)     formal_verify ;;
        soak-runner)       soak_runner ;;
        chaos-runner)      chaos_runner ;;
        auto-bisect)       auto_bisect ;;
        create-revert-pr)  create_revert_pr "$@" ;;
        ci-budget-check)   budget_check ;;
        ''|-h|--help)      usage; return 0 ;;
        *)                 err "未知 helper: ${key}"; usage >&2; return "${EINVAL}" ;;
    esac
}

main "$@"
