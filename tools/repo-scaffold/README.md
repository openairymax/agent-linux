<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# repo-scaffold — AirymaxOS 八子仓脚手架一致性检查

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

八子仓（kernel / services / security / memory / cognition / cloudnative /
system / tests-linux）是 AirymaxOS 的组装单元。每个子仓必须在检出后即具备
公共骨架、许可证标识与真实可解析的构建入口，否则镜像构建与门禁会在更晚
阶段以更难定位的方式失败。本工具以
[manifest.yaml](manifest.yaml) 为**唯一事实源（SSoT）**，静态校验
`.gitmodules` 登记、骨架文件、许可证标识、构建入口，以及构建入口是否真实
引用存在且非空的源文件（决策 F1：最小可编译骨架，非桩）。

工具**只做清单 ↔ 文件系统的交叉比对**，不重复实现构建逻辑，也不代替 CI
编译；所有检查读取真实文件，无桩实现。

权威依据：

| 内容 | 出处 |
|------|------|
| 最小可编译骨架、每仓至少一个真实入口（决策 F1） | `docs/docs-linux/10-architecture/07-directory-structure.md` §3.6 |
| 九项公共骨架文件 | 同上 附录 A |
| kernel 为 Linux 6.6 完整 fork（Model A） | 同上 §2.2、§4.1 |
| 不得删除公共骨架（反模式） | 同上 §11.3 |

## 检查项

| # | 检查项 | 规则 |
|---|--------|------|
| 1 | 清单 schema 合法 | 必填字段、类型、取值、八仓唯一、路径安全 |
| 2 | `.gitmodules` 登记一致 | 八仓无遗漏、无未登记多余项；`excluded` 已登记且不与八仓重叠 |
| 3 | 八子仓已检出 | 每个 `path` 都是目录 |
| 4 | 公共骨架 | 每个骨架文件存在且非空 |
| 5 | 许可证标识 | 许可证文件首部包含规定标识 |
| 6 | 构建入口 | `CMakeLists.txt` / `Kbuild` / `Makefile` 存在且非空 |
| 7 | 可编译入口 | 入口真实引用存在且非空的源文件 |

清单 schema 不合法时仅报告该项并短路其余检查，避免在脏数据上生成噪声结论。

### 构建入口解析

| 入口 | 解析规则 |
|------|----------|
| `CMakeLists.txt` | `add_subdirectory()` 递归一级子目录；`add_executable()` / `add_library()` 的源文件参数须存在且非空，至少引用一个源文件 |
| `Kbuild` | `obj-m` / `obj-y` 的 `.o` 映射为 `.c` 或 `.S`；`<mod>-objs` 复合模块跳过自身 `.o`；`obj-m := dir/` 递归读取 `dir/Kbuild` 或 `dir/Makefile` |
| `Makefile`（kernel） | 以 `entry_probe`（`init/main.c`）证明源码树非空壳，不另建 README/LICENSE 副本 |

### kernel 例外建模

kernel 是 Linux 6.6 完整 fork，目录与文件沿用上游原生布局
（`README` / `COPYING` / `Kbuild` / `Makefile`）。清单以 `skeleton: linux`
剖面显式建模，并以 `entry_probe` 断言源码树非空，避免为满足通用骨架而在
kernel 内复制 `README.md` / `LICENSE`，形成同一内容的两处事实源。

## 使用方法

```bash
# 人类可读完整报告（默认自动探测仓库根）
python3 tools/repo-scaffold/repo_scaffold.py

# CI 模式：精简输出，仅以退出码判定
python3 tools/repo-scaffold/repo_scaffold.py --check

# 指定仓库根与清单
python3 tools/repo-scaffold/repo_scaffold.py --check \
  --repo-root . --manifest tools/repo-scaffold/manifest.yaml
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部检查通过 |
| `1` | 检查失败 |
| `2` | 用法 / 环境错误（清单缺失、YAML 非法、仓库根不可用等） |

`--check` 输出 `REPO-SCAFFOLD: PASS (N/N checks)`，失败时逐项列出证据。

## 维护清单

- **新增子仓**：先在 `subrepos` 登记 `path` / `skeleton` / `license` /
  `build_entry`，再在 `.gitmodules` 添加 submodule，最后运行 `--check`
  确认无漂移；`subrepos` 必须恰好 8 项，若子仓集合变化须同步决策 F1 文档。
- **调整骨架**：修改 `skeletons` 剖面后，须同步仓库内真实文件；`linux`
  剖面只在 kernel 原生布局变化时调整。
- **新增构建入口类型**：在 `KINDS_BY_ENTRY` 与 `check_entry` 增加解析分支，
  并在本 README 的解析规则表登记，避免两处语义漂移。
- **许可证变更**：修改 `licenses` 剖面后，须确认对应文件的标识行同步更新。

## CI 接入

- **管理仓 `os-chk` 注册表**：以门禁 `OS-BUILD-REPO-SCAFFOLD`（P0 /
  category `build`）登记，由 `tools/os-chk/os_chk.py --check` 统一编排。
- **`os-chk-verify.yml`**：`paths` 覆盖本目录，安装 `requirements-dev.txt`
  并运行 pytest。

## 单元测试

```bash
pip install -r tools/repo-scaffold/requirements-dev.txt
python3 -m pytest tools/repo-scaffold/tests/ -q
```

测试以真实清单为基线，在临时目录合成最小合规仓库，再逐项施加受控变异，
逐条断言各检查器的失败语义；CLI 同时在合成仓库与真实仓库上做端到端回归。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
