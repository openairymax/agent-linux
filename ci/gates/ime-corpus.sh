#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/ime-corpus.sh — 中文输入语料回归（QG-09）
#
# 用法:
#   ci/gates/ime-corpus.sh [--root DIR]
#
# 判据本体在 crates/boot-ime/tests/corpus/main.rs：3,000 句冻结语料逐词
# 按默认首选提交，首选字准确率 ≥ 95%、会话拒绝 0、按键 p99 ≤ 50 ms。
# 语料由 words.tsv 的 3 个时间前缀 × 20 个主语 × 50 个谓语交叉生成并入库
# 冻结；生成规则写在 sentences.tsv 与 main.rs 的文件头，生成器不入库。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/tui-gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/tui-gate.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/ime-corpus.sh [--root DIR]
EOF
}

tui_cli usage "$@"

tui_setup "${BASH_SOURCE[0]}"

group "tui: ime corpus"
if ! cargo_run test --offline --locked -p boot-ime --test corpus -- --nocapture; then
    endgroup
    die "中文输入语料回归未通过"
fi
endgroup
say "中文输入语料通过：3,000 句首选准确率 ≥ 95%，崩溃 0，p99 ≤ 50 ms"
