<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# defconfig-matrix — AirymaxOS defconfig 矩阵一致性检查

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

`airy_defconfig` 是 A-UCS（Unified Configuration Subsystem）在构建期的物理
承载。本工具以 [manifest.yaml](manifest.yaml) 为**唯一事实源（SSoT）**，静态
校验「支持架构 × `airy_defconfig` 形态 × CI 构建矩阵 × 必备 CONFIG × Kbuild
引用」五者彼此一致，把散落在 `.github/workflows/ci-airy.yml` 与 `ci-kernel.yml`
中的重复映射收敛到一处清单，并检出任何一侧的漂移。

工具**只做清单与仓库/工作流的交叉比对**，不重复实现构建逻辑；所有检查均读取
真实文件内容，无桩实现。

权威依据：

| 内容 | 出处 |
|------|------|
| `OS-BUILD-026`（各架构 airy_defconfig 为全配置基线） | `docs/docs-linux/70-build-system/02-kconfig-system.md` §10 |
| `OS-BUILD-027`（`KCONFIG_ALLCONFIG` 可求解） | 同上 §7 |
| `OS-BUILD-028`（每支持架构必须有 airy_defconfig） | 同上 §7.1 / §10 |
| `OS-KER-025`（`obj-$(CONFIG_*)` 引用须有 config 声明） | 同上 §10 |
| 编译验证矩阵 / 管理仓 2-job 上限 | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` |

## 检查项

| # | 检查项 | 规则 |
|---|--------|------|
| 1 | 清单 schema 合法（必填字段、arch 唯一、取值、路径形态） | 本工具约定 |
| 2 | 清单 ↔ 文件系统双向一致 | `OS-BUILD-028` |
| 3 | defconfig 格式（首行 SPDX / CONFIG 语法 / 无重复键） | `OS-STD-STY-007`（沿 50 卷） |
| 4 | 必备 CONFIG 齐备 | `OS-BUILD-026` |
| 5 | `CONFIG_LSM` 含清单登记的 LSM 名 | `OS-BUILD-026` |
| 6 | CI 构建矩阵与清单一致（ci-airy 精确匹配 / ci-kernel 登记子集） | `OS-BUILD-028` |
| 7 | 必备 CONFIG 清单逐项相等（ci-airy 的 `required=(...)` / kernel gate 的 `REQUIRED_CONF`） | `OS-BUILD-026` |
| 8 | `obj-$(CONFIG_*)` 引用均有 Kconfig 声明 | `OS-KER-025` |

清单 schema 不合法时，仅报告该项并短路其余检查（避免在脏数据上生成噪声结论）。

## 使用方法

```bash
# 人类可读完整报告（默认自动探测仓库根）
python3 tools/defconfig-matrix/defconfig_matrix.py

# CI 模式：精简输出，仅以退出码判定
python3 tools/defconfig-matrix/defconfig_matrix.py --check

# 指定仓库根 / 清单
python3 tools/defconfig-matrix/defconfig_matrix.py \
  --repo-root . --manifest tools/defconfig-matrix/manifest.yaml --check
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部检查通过 |
| `1` | 一项或多项检查失败 |
| `2` | 用法 / 环境错误（清单缺失、YAML 非法、内核源码目录缺失等） |

`--check` 输出 `DEFCONFIG-MATRIX: PASS (N/N checks)`，失败时逐项列出证据。

## 维护清单

新增受支持架构时：先在本表 `architectures` 登记（`arch` / `cross` / `kind` /
`build` / `configs`），再落地 `kernel/<configs>` 文件，并在 `ci-airy.yml`
（若 `build: true`）与 `ci-kernel.yml` 的矩阵、`ci/gates/kernel-refbuild.sh`
的 `REQUIRED_CONF` 清单同步；随后运行本工具确认无漂移。`build: true` 要求
`kind: fragment`（CI 固定走 `scripts/kconfig/merge_config.sh` 三步法）。

## CI 接入

- **管理仓 `ci-airy.yml`（`airy-static` job）**：以新增 step 调用 `--check`，
  遵守管理仓「每 workflow ≤ 2 job」的上限，不新增 job。
- **管理仓 `os-chk` 注册表**：以门禁 `OS-BUILD-MATRIX`（P0 / category `build`）
  登记，由 `tools/os-chk/os_chk.py --check` 统一编排。

## 单元测试

```bash
pip install -r tools/defconfig-matrix/requirements-dev.txt
python3 -m pytest tools/defconfig-matrix/tests/ -q
```

测试以 `tmp_path` 构造受控合成仓库，逐项断言各检查器的通过/拒绝语义，并在
真实仓库上运行一例端到端回归。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
