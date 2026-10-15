<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# btf-integration — AirymaxOS BTF / eBPF 基线一致性检查

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

Agent 观测面（L2 层）的 eBPF 程序必须基于 **BTF 重定位（CO-RE）**，不得硬编码
结构体偏移；这要求每个受支持架构的 `airy_defconfig` 同时开启 eBPF 基线与 BTF
基线，且构建期提供 `pahole`、构建产物中真实携带 BTF。本工具以
[manifest.yaml](manifest.yaml) 为**唯一事实源（SSoT）**，静态校验「受检架构 ×
eBPF 基线 × BTF 基线 × tracing 链路 × CI 供给」五者彼此一致，把散落在内核
片段与 `.github/workflows/ci-kernel.yml`、`.github/workflows/ci-airy.yml` 中的
隐含约束收敛到一处清单，并检出任何一侧的漂移。

受检架构不在本清单重复登记，统一复用 [tools/defconfig-matrix/manifest.yaml](../defconfig-matrix/manifest.yaml)
中 `build: true` 的条目，**避免第二事实源**。工具**只做清单与内核片段/工作流的
交叉比对**，不重复实现构建逻辑；所有检查均读取真实文件内容，无桩实现。

权威依据：

| 内容 | 出处 |
|------|------|
| `OS-KER-106`（defconfig 须开启 `CONFIG_BPF` / `CONFIG_BPF_SYSCALL` / `CONFIG_BPF_JIT` / `CONFIG_BPF_EVENTS` / `CONFIG_DEBUG_INFO_BTF`） | `docs/docs-linux/90-observability/02-ebpf-probes.md` |
| `OS-OBS-012`（须开启 `CONFIG_DEBUG_INFO_BTF`，构建产物中提供 vmlinux 的 BTF） | 同上 |
| `CONFIG_DEBUG_INFO_BTF` 的 Kconfig 依赖（互斥 `DEBUG_INFO_REDUCED` / `DEBUG_INFO_SPLIT`，`DEBUG_INFO` 由 DWARF choice 项 `select`） | `kernel/lib/Kconfig.debug` |
| `CONFIG_BPF_EVENTS` 的 `if FTRACE` / `depends on BPF_SYSCALL` 邻接关系 | `kernel/kernel/trace/Kconfig` |
| Linux 6.6 的 BTF 由 `gen_btf()` 以 `.BTF` 段内嵌进 vmlinux（无独立 `vmlinux.btf` 文件） | `kernel/scripts/link-vmlinux.sh` |

## 检查项

| # | 检查项 | 规则 |
|---|--------|------|
| 1 | 清单 schema 合法（必填字段、取值、路径形态） | 本工具约定 |
| 2 | 受检片段存在（清单 `build: true` 的 `configs` ↔ 内核树） | `tools/defconfig-matrix` |
| 3 | eBPF 基线齐备（每个受检片段逐项含 `bpf_configs`） | `OS-KER-106` |
| 4 | BTF 基线齐备（含 `btf_configs` / 互斥项未设 / 命中 DWARF 形态） | `OS-OBS-012` |
| 5 | tracing 链路前置齐备（`FTRACE` + `KPROBES`） | `OS-KER-106` |
| 6 | CI 提供 `pahole` 且断言 `.BTF` 产物 | `OS-OBS-012` |

清单 schema 不合法时仅报告该项；受检片段缺失或矩阵清单不可用时，报告至第 2 项
并短路其余内容类检查（避免在脏数据上生成噪声结论）。

> **SSoT 注册一致性不在此重复实现**：规则 ID 与文档的登记一致性由门禁
> `OS-IRON-015`（`tools/validate-ssot.py`）统一保证，本工具只负责基线内容一致。

## 使用方法

```bash
# 人类可读完整报告（默认自动探测仓库根）
python3 tools/btf-integration/btf_integration.py

# CI 模式：精简输出，仅以退出码判定
python3 tools/btf-integration/btf_integration.py --check

# 指定仓库根 / 清单
python3 tools/btf-integration/btf_integration.py \
  --repo-root . --manifest tools/btf-integration/manifest.yaml --check
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部检查通过 |
| `1` | 一项或多项检查失败 |
| `2` | 用法 / 环境错误（清单缺失、YAML 非法、内核源码目录缺失等） |

`--check` 输出 `BTF-INTEGRATION: PASS (N/N checks)`，失败时逐项列出证据。

## 维护清单

新增受支持架构时无需改动本清单：只要在 [tools/defconfig-matrix/manifest.yaml](../defconfig-matrix/manifest.yaml)
登记并置 `build: true`，本工具即自动将其纳入受检范围，并要求其片段齐备 eBPF /
BTF / tracing 三组基线。

调整基线（如 `OS-KER-106` 增补开关）时：先改本清单的 `bpf_configs` /
`btf_configs` / `tracing_chain`，再同步各架构片段的 `airy_defconfig`，随后运行
本工具确认无漂移。

> Linux 6.6 不产出独立的 `vmlinux.btf`：BTF 以 `.BTF` 段内嵌进 `vmlinux`。因此
> `btf_artifacts` 登记的是 `.BTF`，CI 以 `readelf -S vmlinux` 断言该段存在；
> 断言 `vmlinux.btf` 文件将永远失败，等同桩校验。

## CI 接入

- **内核仓 `ci-kernel.yml`（`kernel-build` / `kernel-verify`）**：两个 job 的
  apt 行均需安装 `dwarves`（提供 `pahole`）；`kernel-verify` 在构建后以
  `readelf -S vmlinux | grep -q '\.BTF'` 断言 BTF 落地。本工具的检查项 6 即
  静态保证该供给存在。
- **管理仓 `ci-airy.yml`（`airy-build` job）**：`airy-build` 以 `airy_defconfig`
  构建全部 4 架构，故其 apt 行亦需安装 `dwarves`，并在构建后以
  `readelf -S vmlinux | grep -q '\.BTF'` 断言 BTF 落地——缺 `pahole` 会令
  `gen_btf()` 直接失败，构建即红。
- **管理仓 `ci-airy.yml`（`airy-static` job）**：以新增 step 调用 `--check`，
  遵守管理仓「每 workflow ≤ 2 job」的上限，不新增 job。
- **管理仓 `os-chk` 注册表**：以门禁 `OS-BUILD-BTF`（P0 / category `build`）
  登记，由 `tools/os-chk/os_chk.py --check` 统一编排。

## 单元测试

```bash
pip install -r tools/btf-integration/requirements-dev.txt
python3 -m pytest tools/btf-integration/tests/ -q
```

测试以 `tmp_path` 构造受控合成仓库，逐项断言各检查器的通过/拒绝语义，并在
真实仓库上运行一例端到端回归。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
