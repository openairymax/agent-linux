<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# ci-matrix — AirymaxOS CI 编译矩阵一致性检查

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

编译验证矩阵（OS-STD-TOOL-161）把「4 配置 × 4 架构 × 2 编译器 − 4」收敛为
28 格，是 PR 合并的硬门禁。本工具以 [manifest.yaml](manifest.yaml) 为
**唯一事实源（SSoT）**，静态校验矩阵定义、调度约束、超时预算与门禁语义
是否与 `.github/workflows/kernel-verify.yml` 逐项一致，并把逐格构建结果按
失败语义归因、汇聚为 PR 标签。

架构维度不重复定义，统一取自
`tools/defconfig-matrix/manifest.yaml` 中 `build: true` 的条目。

工具**只做清单 ↔ workflow ↔ 结果摘要的交叉比对**，不重复实现构建逻辑，
也不代替 CI 执行编译；所有检查读取真实文件，无桩实现。

权威依据：

| 内容 | 出处 |
|------|------|
| `OS-STD-TOOL-161`（28 配置矩阵） | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` §3.1 |
| 矩阵生成与调度（`fail-fast` / `max-parallel`） | 同上 §3.2 |
| 编译命令与门禁模板 | 同上 §3.3 |
| `OS-STD-TEST-011`（单 job ≤60 分钟） | 同上 §9.4 |
| 失败分类与 PR 标签 | 同上 §10.1 |

## 检查项

| # | 检查项 | 规则 |
|---|--------|------|
| 1 | 清单 schema 合法（必填字段与类型） | 本工具约定 |
| 2 | 架构维度可解析且非空 | 复用 defconfig 矩阵清单 |
| 3 | workflow 与矩阵 job 存在并声明 `strategy.matrix` | `OS-STD-TOOL-161` |
| 4 | 矩阵三维与清单逐项相等 | `OS-STD-TOOL-161` |
| 5 | 展开格数等于 `expected_cells`（28） | `OS-STD-TOOL-161` |
| 6 | `exclude` 项合法、命中真实组合且与 workflow 一致 | §3.2 |
| 7 | `fail-fast: false` | §3.2 |
| 8 | `max-parallel` 等于清单值 | §3.2 |
| 9 | 矩阵 job 超时等于清单值且 ≤60 分钟 | `OS-STD-TEST-011` |
| 10 | 每 workflow ≤2 job 且每个 job 声明 `timeout-minutes` | §9.4 |
| 11 | 编译门禁语义片段齐备（error 阻断 / Airymax warning 收敛） | §3.3 |

清单 schema 不合法时仅报告该项并短路其余检查，避免在脏数据上生成噪声结论。

## 使用方法

```bash
# 人类可读完整报告（默认自动探测仓库根）
python3 tools/ci-matrix/ci_matrix.py

# CI 模式：精简输出，仅以退出码判定
python3 tools/ci-matrix/ci_matrix.py --check

# 展开矩阵：逐行输出 arch/compiler/config，末行为 TOTAL
python3 tools/ci-matrix/ci_matrix.py --list

# 归因逐格结果并输出 PR 标签
python3 tools/ci-matrix/ci_matrix.py --summary matrix-result.json
```

### 结果摘要格式

`--summary` 接受 `{"cells": [...]}` 或裸列表，每个单元必须含三个维度与结论：

```json
{
  "cells": [
    {"config": "defconfig", "arch": "x86_64",
     "compiler": "gcc", "conclusion": "success"},
    {"config": "airy_defconfig", "arch": "riscv",
     "compiler": "clang", "conclusion": "failure"}
  ]
}
```

- 仅 `conclusion: success` 视为通过；`skipped` / `cancelled` 不得静默放行。
- 缺失格、重复格、未知维度取值一律判失败。
- 归因优先级：编译器可移植性（clang 失败而 gcc 通过）→ 架构抽象缺失
  （非 x86_64 失败而同配置 x86_64 通过）→ 配置类语义（`allnoconfig` /
  `allmodconfig` / `defconfig` / `airy_defconfig`）→ 通用编译错误。

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部检查通过 / 摘要全绿 |
| `1` | 检查失败 / 摘要存在失败、缺失或未知格 |
| `2` | 用法 / 环境错误（清单缺失、YAML/JSON 非法、架构源不可读等） |

`--check` 输出 `CI-MATRIX: PASS (N/N checks)`，失败时逐项列出证据。

## 维护清单

- **新增配置 / 编译器 / 排除项**：先改本清单，再同步 `kernel-verify.yml` 的
  `matrix` 段，随后运行 `--check` 确认无漂移。
- **新增支持架构**：只改 `tools/defconfig-matrix/manifest.yaml` 并置
  `build: true`，本工具自动纳入；不要在 `ci-matrix` 重复登记架构名。
- **调整超时预算**：修改 `budget.timeout_minutes`，但不得超过
  `OS-STD-TEST-011` 的 60 分钟上限；超时子集须拆分到 nightly。

## CI 接入

- **管理仓 `kernel-verify.yml`（`kernel-verify` job）**：以新增 step 调用
  `--check`，遵守「每 workflow ≤2 job」上限，不新增 job。
- **管理仓 `os-chk` 注册表**：以门禁 `OS-BUILD-CI-MATRIX`（P0 / category
  `build`）登记，由 `tools/os-chk/os_chk.py --check` 统一编排。

## 单元测试

```bash
pip install -r tools/ci-matrix/requirements-dev.txt
python3 -m pytest tools/ci-matrix/tests/ -q
```

测试以真实清单为基线做受控变异，逐项断言各检查器的通过/拒绝语义，并在
真实仓库上运行 `--check` 与 `--list` 端到端回归。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
