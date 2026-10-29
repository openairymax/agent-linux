<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# nightly — AirymaxOS nightly pipeline 契约检查

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

nightly 流水线承担 PR 无法覆盖的长时验证：形式化验证、72h soak、
混沌注入，以及失败后的自动二分回滚与 CI 总时长预算核对。本工具以
[manifest.yaml](manifest.yaml) 为**唯一事实源（SSoT）**，静态校验
`.github/workflows/nightly.yml` 的触发方式、并发、权限、job 拓扑、
超时预算与 helper 调用契约是否与清单逐项一致。

工具**只做清单 ↔ workflow ↔ 文件系统的交叉比对**，不重复实现测试逻辑，
也不代替 CI 执行验证；所有检查读取真实文件，无桩实现。

权威依据：

| 内容 | 出处 |
|------|------|
| nightly 结果不阻断 PR | `docs/docs-linux/120-development-process/08-ci-cd-pipeline.md` §6.1 |
| 触发方式（cron `0 2 * * *` + `workflow_dispatch`） | 同上 §6.2 |
| 全局单实例、不取消进行中的运行 | 同上 §9.2 |
| 未落地 helper 的 SKIP 降级 | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` §5.4、§5.5 |
| `OS-STD-TEST-011`（CI 总时长 ≤60 分钟） | 同上 §9.4 |
| 托管 runner 单 job 硬上限 6 小时 | GitHub Actions 平台约束 |

## 检查项

| # | 检查项 | 规则 |
|---|--------|------|
| 1 | 清单 schema 合法（必填字段、类型、helper 引用） | 本工具约定 |
| 2 | 触发方式为 cron + `workflow_dispatch`，且无 PR/push 触发 | §6.1、§6.2 |
| 3 | 并发组为 `nightly` 且 `cancel-in-progress: false` | §9.2 |
| 4 | 权限收敛为 `contents: read` | 最小权限原则 |
| 5 | job 名与 `needs` 拓扑和清单一致 | 本工具约定 |
| 6 | runner 标签匹配且超时不超过托管上限 | 平台约束 |
| 7 | job 超时与清单逐项相等 | `OS-STD-TEST-011` |
| 8 | 脚本引用的 shell 变量均有定义 | 本工具约定 |
| 9 | helper 脚本已登记在 helper surface 文档 | `tools/README.md` |
| 10 | helper 的调用、守卫、`SKIP:` 标记与落地状态自洽 | §5.4、§5.5 |
| 11 | 预算检测以 `--max-minutes 60` 调用 | `OS-STD-TEST-011` |

清单 schema 不合法时仅报告该项并短路其余检查，避免在脏数据上生成噪声结论。

### helper 状态语义

| 状态 | 要求 |
|------|------|
| `deferred` | 载体正文内必须有 `if [ -f <path> ]` 守卫与 `SKIP:` 标记，且文件不得存在于磁盘 |
| `available` | 文件必须存在，且不得被存在性守卫包裹 |

契约的载体由顶层 `helper_gate` 决定：登记后守卫、argv 与 `SKIP:` 分支住在
该门禁脚本里，workflow 只保留 `ci/ci.sh nightly-helpers <key>` 一行，逻辑可
在本地重放；未登记时退回「workflow 内联守卫」形态。两种形态下都要求 workflow
文本出现 `nightly-helpers <key>`（内联形态则要求出现真实调用），门禁登记却
不可读、或调用与门禁脱钩，均判失败。

脚本一旦落地而清单仍标 `deferred`，检查直接失败并提示转为 `available`；
反向漂移同样失败。该约束保证「未实现即显式跳过、已实现即真实执行」，
避免桩实现悄悄进入流水线。

## 使用方法

```bash
# 人类可读完整报告（默认自动探测仓库根）
python3 tools/nightly/nightly.py

# CI 模式：精简输出，仅以退出码判定
python3 tools/nightly/nightly.py --check

# 指定清单以外的仓库根
python3 tools/nightly/nightly.py --check --manifest tools/nightly/manifest.yaml
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部检查通过 |
| `1` | 检查失败 |
| `2` | 用法 / 环境错误（清单缺失、YAML 非法、workflow 不可读等） |

`--check` 输出 `NIGHTLY: PASS (N/N checks)`，失败时逐项列出证据。

## 维护清单

- **新增 nightly job**：先改本清单的 `jobs` 段，再同步 `nightly.yml` 的
  job 定义与 `needs` 拓扑，随后运行 `--check` 确认无漂移。
- **新增 helper**：在 `helpers` 段登记路径、状态与必要参数，在
  `tools/README.md` 的 helper surface 表登记 basename，再接入对应 job。
- **helper 落地**：把 `status` 改为 `available`，在 `helper_gate` 指向的门禁
  脚本里移除 `if [ -f ... ]` 守卫与 `SKIP:` 分支（未登记门禁则改 job 脚本），
  并确认 `os-chk-verify.yml` 已纳入其测试。
- **调整预算**：修改 `budget.argv` 时须同步 job 2 的调用，且不得超过
  `OS-STD-TEST-011` 的 60 分钟上限。

## CI 接入

- **`nightly.yml`（job 1 首步）**：以 `python3 tools/nightly/nightly.py
  --check` 自校验契约，先于任何长时任务执行。
- **管理仓 `os-chk` 注册表**：以门禁 `OS-BUILD-NIGHTLY`（P0 / category
  `build`）登记，由 `tools/os-chk/os_chk.py --check` 统一编排。
- **`os-chk-verify.yml`**：`paths` 覆盖本目录与 `nightly.yml`，安装
  `requirements-dev.txt` 并运行 pytest。

## 单元测试

```bash
pip install -r tools/nightly/requirements-dev.txt
python3 -m pytest tools/nightly/tests/ -q
```

测试以真实清单与 workflow 为基线做受控变异，逐项断言各检查器的
通过/拒绝语义；文件类检查使用临时目录合成仓库，CLI 在真实仓库上做
端到端回归。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
