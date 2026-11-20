#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lint.sh — 仓库级 CI 层自检
#
# 用法: ci/lint.sh [--no-distro]
#
# 检查十六件事：
#   1. workflow 薄化与命名约束（ci/checks/check-workflows.py）；
#   2. ci/ 与 tools/ 下每个 shell 脚本可被 bash -n 解析；
#   3. 函数名不超过 15 字节（ci/checks/name_lint.py，N001，覆盖 shell 与 python）；
#   4. 代码度量棘轮（ci/gates/quality-metrics.sh → ci/checks/quality_metrics.py：新增
#      超标函数与新增重复片段判红，存量按基线只减不增）；
#   5. 验收剖面归一化的行为（ci/lib/profile.sh）；
#   6. 门禁脚本里「声明后从未使用」的 readonly 常量（无外部依赖）；
#   7. ci/tests 下的门禁分支回归；
#   8. 有 shellcheck 就用上，没有只告警——本地开发者不该被工具缺失拦住；
#   9. distro 子仓静态门禁（ci/gates/distro-lint.sh，含其工具单测）；
#  10. 基底耦合点两向对账（ci/gates/coupling-scan.sh，成册件 §10 主表）；
#  11. 模型资产通道门禁（ci/gates/model-manifest.sh，声明面与离线实物面）；
#  12. 功能归属册三态对账（ci/gates/completeness.sh，docs-linux 功能面逐条归账）；
#  13. 零桩扫描（ci/gates/no-stub.sh，QG-02）；
#  14. 死代码扫描（ci/gates/dead-code.sh，QG-03）；
#  15. 文件头许可证与版权行（ci/checks/header_lint.py，H001/H002）；
#  16. docs-linux 文档树的悬空相对链接（ci/gates/docs-links.sh，DOC-02）。
#
# 第 13、14 项把「禁桩」从文字变成判据：todo!/unimplemented!、产品代码里的
# unreachable!、恒 Ok(()) 与 unsafe 逐条判负，allow(dead_code) 必须写理由，
# 无引用的 pub 项进人工复核清单。真实树零命中只说明今天干净，判据本身由
# ci/tests/test_no_stub.sh 与 test_dead_code.sh 用夹具覆盖，第 7 项会跑它们。
#
# 第 9 项此前只在流水线里跑，本地「两个入口都绿」靠人记：run 40938020614
# 的 os-chk-verify 就红在 distro 的一条旧语义断言上，而提交前只跑过本文件。
# 现在一条命令覆盖两侧口径。CI 里 os-chk-verify 不再单列 distro-lint 步骤
# ——同一份工作跑两遍只会加长每轮耗时。`--no-distro` 留给快速迭代。
#
# 第 6 项是第 8 项的兜底：本地没有 shellcheck 时 SC2034 不会报，CI 的
# 24.04 自带 shellcheck 会报，于是出现「本地全绿、CI 红」（0.0.3
# 的 release-deps.sh 就是这样挂掉的）。只查 ci/gates 下的叶子脚本：这些
# 文件不被 source，常量必须自用，判定无歧义；ci/lib 的常量供他人引用，
# 不能按同一条规则判。
#
# 第 11 项与第 9 项同类：model-manifest 过去只在 os-chk-verify 里跑，配方
# 把逐条建组收成循环后门禁仍按字面量断言，于是本地全绿而流水线红。判据已
# 改为按清单断言，本文件同时把它纳入本地入口。
#
# 第 12 项把「docs-linux 的功能面归账」变成可执行判据。归属册在 docs-closed，
# 单仓检出看不到它，门禁自身退 0 并打印不适用；开发机上则逐条核对三态、
# 统计与证据路径。判据本身由 ci/tests/test_completeness.sh 用夹具覆盖。
#
# 与 distro/ci/run-lint.sh 的分工：后者管 distro 子模块自己的单元测试与
# 打包脚本，本文件覆盖仓库根这一层（门禁 + workflow），末段以门禁入口
# 转调前者。两边逻辑不复制，只有调用方向。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=lib/log.sh
. "${ROOT}/ci/lib/log.sh"

no_distro=0
while [ "$#" -gt 0 ]; do
    case "$1" in
    --no-distro) no_distro=1 ;;
    *)
        err "未知参数: $1（可用: --no-distro）"
        exit 2
        ;;
    esac
    shift
done

rc=0

group "workflow 薄化与命名约束"
if python3 "${ROOT}/ci/checks/check-workflows.py"; then
    say "通过"
else
    err "ci/checks/check-workflows.py 未通过"
    rc=1
fi
endgroup

group "bash 语法"
# tools/ 顶层也有 shell 入口（venv-bootstrap.sh）。此前只扫 ci/，这类脚本
# 语法错误要到开发者第一次执行时才暴露。
n=0
while IFS= read -r f; do
    n=$(( n + 1 ))
    bash -n "${f}" || { err "语法错误: ${f}"; rc=1; }
done < <( { find "${ROOT}/ci" -type f -name '*.sh'
            find "${ROOT}/tools" -maxdepth 1 -type f -name '*.sh'; } | sort)
say "已解析 ${n} 个脚本"
endgroup

group "函数名长度"
# 0.0.4 收尾把 139 个超长函数名（shell 21、python 118）压到 15 字节以内。
# 压完不留判据，下一个 helper 只要叫 airy_assets_wanted 就能把规范重新
# 拉开，而这道门禁是唯一能当场拦住它的地方。distro 子仓一并覆盖：同一个
# 判据写两份实现，改阈值时必然漏改一处；`--no-distro` 时只扫本层，与下面
# 跳过 distro 门禁同口径。
roots=(ci tools system)
if [ "${no_distro}" -eq 0 ]; then
    roots=(ci tools distro system)
fi
if python3 "${ROOT}/ci/checks/name_lint.py" "${roots[@]}"; then
    say "通过"
else
    err "ci/checks/name_lint.py 未通过（函数名超过 15 字节）"
    rc=1
fi
endgroup

group "代码度量棘轮"
# QG-01/QG-02 的「重复率 <3%、圈复杂度 <2.5」此前只有文字没有判据，存量
# 因此停在均值 3.70、重复率 6.17% 也没人看见。基线按现状登记、只减不增：
# 新增超标函数与新增重复片段当场判红，差额每轮打印。
# 转调门禁入口而不是直连 ci/checks/quality_metrics.py：`--update` 的「只减不增」
# 政策只在 ci/gates/quality-metrics.sh 一处实现，两个入口各写一份迟早出现
# 一条路能抬高基线而另一条不认。缺省根由门禁按实装位置取，`--no-distro`
# 时只把子集目录透传过去。
qm=()
if [ "${no_distro}" -eq 1 ]; then
    qm=(--dir "${ROOT}/ci" --dir "${ROOT}/tools" --dir "${ROOT}/system")
fi
if bash "${ROOT}/ci/ci.sh" quality-metrics --check "${qm[@]}"; then
    say "通过"
else
    err "代码度量棘轮未通过（ci/ci.sh quality-metrics：新增超标函数或新增重复片段）"
    rc=1
fi
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

group "基底耦合点对账"
# 成册件 §10 主表是耦合点的唯一事实源，门禁的两向对账只在真实树上跑才有
# 意义：回归脚本用的是夹具，判不了「本仓漏登记」。未检出 docs 树时门禁
# 自身退 0 并打印不适用，单仓检出不会因此变红。
if bash "${ROOT}/ci/ci.sh" coupling-scan; then
    say "通过"
else
    err "基底耦合点对账未通过（ci/ci.sh coupling-scan）"
    rc=1
fi
endgroup

group "功能归属册对账"
# docs-linux 的每一块功能面都必须在归属册上占一行三态。册子在 docs-closed，
# 只在开发机上存在；未检出时门禁自身退 0 并打印不适用，与上一项同口径。
if bash "${ROOT}/ci/ci.sh" completeness; then
    say "通过"
else
    err "功能归属册对账未通过（ci/ci.sh completeness）"
    rc=1
fi
endgroup

group "文档悬空链接"
# 目录编号重组过两轮，正文里的相对链接按旧布局写，读者点到 404 才发现。
# 门禁是 DOC-02 的唯一执行者，而它此前没有执行者；判据本身由
# ci/tests/test_docs_links.sh 用夹具覆盖，这里跑真实树。单仓检出无文档树时
# 门禁自身退 0 并打印不适用，与上一项同口径。
if bash "${ROOT}/ci/ci.sh" docs-links; then
    say "通过"
else
    err "文档悬空链接未通过（ci/ci.sh docs-links）"
    rc=1
fi
endgroup

group "文档契约一致性"
# 十九个模块的入口映射在册子（modmap.toml）与投影（modmap.rs）各存一份：
# 评审读册子，编译读投影。两份都在本仓，单仓检出即可判，因此不设跳过分
# 支。判据本体在 ci/checks/modmap_conform.py，负例由 test_doc_contract.sh
# 覆盖并随上面的回归组一起跑。
if bash "${ROOT}/ci/ci.sh" doc-contract; then
    say "通过"
else
    err "文档契约一致性未通过（ci/ci.sh doc-contract）"
    rc=1
fi
endgroup

if [ "${no_distro}" -eq 0 ]; then
    group "distro 子仓静态门禁"
    # 转调门禁入口而不是直连 distro/ci/run-lint.sh：参数语义、group 头与
    # 退出码都由 ci/gates/distro-lint.sh 一处决定，本文件不再解释它。
    if bash "${ROOT}/ci/ci.sh" distro-lint; then
        say "通过"
    else
        err "distro 静态门禁未通过（ci/ci.sh distro-lint）"
        rc=1
    fi
    endgroup
else
    warn "--no-distro: 跳过 distro 子仓静态门禁"
fi

group "模型资产通道"
# 声明面与离线实物面都在本机可跑，不需要安装根，因此没有理由留给流水线。
if bash "${ROOT}/ci/ci.sh" model-manifest; then
    say "通过"
else
    err "模型资产通道未通过（ci/ci.sh model-manifest）"
    rc=1
fi
endgroup

group "零桩扫描"
# QG-02 的判据此前只写在方案里，真实树全绿也证明不了判据有效；正反例由
# ci/tests/test_no_stub.sh 用夹具覆盖。目录传绝对路径，本文件不依赖调用者
# 的当前目录。
if bash "${ROOT}/ci/ci.sh" no-stub "${ROOT}/system/tui"; then
    say "通过"
else
    err "零桩扫描未通过（ci/ci.sh no-stub system/tui）"
    rc=1
fi
endgroup

group "死代码扫描"
# 与上一项同批：allow(dead_code) 无理由判负，无引用的 pub 项进复核清单。
# 复核清单不改变退出码，因此这里绿不代表 pub 面已清理干净，清单要看。
if bash "${ROOT}/ci/ci.sh" dead-code "${ROOT}/system/tui"; then
    say "通过"
else
    err "死代码扫描未通过（ci/ci.sh dead-code system/tui）"
    rc=1
fi
endgroup

group "文件头许可证与版权"
# 编码规范要求每个源文件头部带 SPDX 许可证与版权行，此前没有执行者：720
# 个自有源文件只有许可证行，tools/repo 下还有一个连许可证都没有。判据与
# 归一化同批落地，否则下一个新文件照样能只写许可证。sdk/ 是生成产物，已带
# 传统版权行，纳入判据防止生成器倒退。
if python3 "${ROOT}/ci/checks/header_lint.py" "${roots[@]}" sdk; then
    say "通过"
else
    err "ci/checks/header_lint.py 未通过（文件头缺许可证或版权行）"
    rc=1
fi
endgroup

if command -v shellcheck >/dev/null 2>&1; then
    group "shellcheck"
    while IFS= read -r f; do
        shellcheck -S warning -x "${f}" || { err "shellcheck: ${f}"; rc=1; }
    done < <( { find "${ROOT}/ci" -type f -name '*.sh'
                find "${ROOT}/tools" -maxdepth 1 -type f -name '*.sh'; } | sort)
    endgroup
else
    warn "未安装 shellcheck，跳过静态检查"
fi

if [ "${rc}" -eq 0 ]; then
    say "== ci lint passed =="
fi
exit "${rc}"
