#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/quality-metrics.sh — 代码度量棘轮（QG-01 / QG-02）
#
# 用法:
#   ci/gates/quality-metrics.sh [--check|--update]
#
# 背景：0.0.2 与 0.0.3 方案把「重复率 < 3%、圈复杂度 < 2.5」写进 QG-01/QG-02，
# 但一直没有判定实现。没有判据的目标值等于没有目标，存量因此停在均值 3.70、
# 重复率 6.19% 也没人看见。本门禁按棘轮落地：
#
#   · 新增函数单函数圈复杂度不得 > 10，新增重复片段一律判红；
#   · 存量按 ci/quality-baseline.json 登记，只减不增（--update 拒绝抬高数量）；
#   · 每次运行都打印与 3% / 2.5 目标的差额，差额不会因为没判据而隐形。
#
# 度量与判定都在 ci/quality_metrics.py：本文件只做参数校验与转调，不复制
# 口径——两处各写一份，改阈值时必然漏改一处。度量根由实装位置决定（它量
# 的就是本仓），因此这里不设 --source：根只在一处定义，避免两处各定一次。
#
#   --check   按基线判定（默认）
#   --update  重写基线；数量不增才允许（改名换登记可以，新欠账不行）
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
MODE="--check"

usage()
{
    cat <<'EOF'
用法: ci/gates/quality-metrics.sh [--check|--update]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check|--update) MODE="$1"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1（可用: --check --update）"; exit "${EINVAL}" ;;
    esac
done

need_cmd python3
python3 "${ROOT}/ci/quality_metrics.py" "${MODE}" || exit 1
say "代码度量棘轮通过"
exit 0
