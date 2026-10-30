#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lint.sh — 仓库级 CI 层自检
#
# 用法: ci/lint.sh
#
# 检查六件事：
#   1. workflow 薄化与命名约束（ci/check-workflows.py）；
#   2. ci/ 下每个脚本可被 bash -n 解析；
#   3. 验收剖面归一化的行为（ci/lib/profile.sh）；
#   4. 门禁脚本里「声明后从未使用」的 readonly 常量（无外部依赖）；
#   5. ci/tests 下的门禁分支回归；
#   6. 有 shellcheck 就用上，没有只告警——本地开发者不该被工具缺失拦住。
#
# 第 4 项是第 6 项的兜底：本地没有 shellcheck 时 SC2034 不会报，CI 的
# ubuntu-22.04 自带 shellcheck 会报，于是出现「本地全绿、CI 红」（0.0.3
# 的 release-deps.sh 就是这样挂掉的）。只查 ci/gates 下的叶子脚本：这些
# 文件不被 source，常量必须自用，判定无歧义；ci/lib 的常量供他人引用，
# 不能按同一条规则判。
#
# 与 distro/ci/run-lint.sh 的分工：后者管 distro 子模块自己的单元测试与
# 打包脚本；本文件只覆盖仓库根这一层（门禁 + workflow）。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=lib/log.sh
. "${ROOT}/ci/lib/log.sh"

rc=0

group "workflow 薄化与命名约束"
if python3 "${ROOT}/ci/check-workflows.py"; then
    say "通过"
else
    err "ci/check-workflows.py 未通过"
    rc=1
fi
endgroup

group "bash 语法"
n=0
while IFS= read -r f; do
    n=$(( n + 1 ))
    bash -n "${f}" || { err "语法错误: ${f}"; rc=1; }
done < <(find "${ROOT}/ci" -type f -name '*.sh' | sort)
say "已解析 ${n} 个脚本"
endgroup

group "验收剖面归一化"
# 剖面在三个 gate 各解析过一份，其中两份把调用方的 true 覆盖成 0，
# 使验收轮悄悄退化成产品轮。这里把归一行为钉成断言。
bad=0
while IFS='|' read -r raw want; do
    got="$(ACCEPTANCE="${raw}" bash -c ". '${ROOT}/ci/lib/profile.sh'; airy_profile")"
    [ "${got}" = "${want}" ] || {
        err "ACCEPTANCE='${raw}' 归一为 '${got}'，应为 '${want}'"; bad=1; }
done <<'CASES'
1|1
true|1
yes|1
0|0
false|0
|0
CASES
if [ "${bad}" -eq 0 ]; then
    say "6 组输入归一正确"
else
    rc=1
fi
endgroup

group "门禁常量自用"
# grep -c 按行计：声明那一行算一次，任何一处使用都会让结果大于 1。
dead=0
while IFS= read -r f; do
    while IFS= read -r name; do
        [ -n "${name}" ] || continue
        uses="$(grep -c "\<${name}\>" "${f}")"
        [ "${uses}" -gt 1 ] || { err "声明后未使用: ${name} (${f#"${ROOT}/"})"; dead=1; }
    done < <(grep -oE '^[[:space:]]*readonly[[:space:]]+[A-Za-z_][A-Za-z0-9_]*=' "${f}" |
             sed -E 's/^.*readonly[[:space:]]+//; s/=.*$//')
done < <(find "${ROOT}/ci/gates" -type f -name '*.sh' | sort)
if [ "${dead}" -eq 0 ]; then
    say "门禁常量均有使用"
else
    rc=1
fi
endgroup

group "门禁分支回归"
# 回归脚本存在但不被执行，就会随门禁一起腐坏：pool-seed 的完整性判据从
# 「目录非空」改成「完成标记」之后，ci/tests 里那条断言仍然按旧语义写，
# 一直是红的却没人看见。放进 lint 的代价是几秒，收益是判据变更必须同时
# 更新断言。
n=0
while IFS= read -r f; do
    n=$(( n + 1 ))
    bash "${f}" || { err "回归失败: ${f#"${ROOT}/"}"; rc=1; }
done < <(find "${ROOT}/ci/tests" -type f -name 'test_*.sh' | sort)
say "已运行 ${n} 个回归脚本"
endgroup

if command -v shellcheck >/dev/null 2>&1; then
    group "shellcheck"
    while IFS= read -r f; do
        shellcheck -S warning -x "${f}" || { err "shellcheck: ${f}"; rc=1; }
    done < <(find "${ROOT}/ci" -type f -name '*.sh' | sort)
    endgroup
else
    warn "未安装 shellcheck，跳过静态检查"
fi

if [ "${rc}" -eq 0 ]; then
    say "== ci lint passed =="
fi
exit "${rc}"
