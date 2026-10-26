<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# kbuild-version — AirymaxOS Kbuild 版本注入一致性检查

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

AirymaxOS 的版本号必须收敛到**一处**：内核树中的供应商版本文件
[`kernel/Makefile.airymaxos`](../../kernel/Makefile.airymaxos) 只承载版本四元组
（`AIRYMAXOS_LTS` / `AIRYMAXOS_MAJOR` / `AIRYMAXOS_MINOR` / `AIRYMAXOS_RELEASE`），
顶层 [`kernel/Makefile`](../../kernel/Makefile) 经 `include` 引入该文件，再由
`filechk_version.h` 以 `filechk` 机制注入为 `include/generated/uapi/linux/version.h`
中的 C 宏。本工具以 [manifest.yaml](manifest.yaml) 为**唯一事实源（SSoT）**，
静态校验「版本四元组 × 版本文件 × 顶层 Makefile 的 `include` × `filechk_version.h`
注入宏 × 契约快照」五者彼此一致，把散落在版本文件、顶层 Makefile 与生成契约中的
隐含约束收敛到一处清单，并检出任何一侧的漂移。

工具**只做清单与源码/契约的交叉比对**，不重复实现 Kbuild 逻辑；所有检查均读取
真实文件内容，无桩实现。四元组只在本清单登记一次，工具的期望值全部由它推导，
**避免第二事实源**。

权威依据：

| 内容 | 出处 |
|------|------|
| `filechk` 版本注入与 `Makefile.airymaxos`（§5） | `docs/docs-linux/70-build-system/01-kbuild-system.md` |
| `Makefile.airymaxos` 供应商版本注入：`include` 指令与注入宏清单（§5.2） | 同上 |
| `OS-STD-049`（`Makefile.airymaxos` 只放版本四元组，特性开关走 Kconfig） | 同上 |
| `OS-BUILD-009`（四元组变更须经发布管理评审并同步 kernel 子仓发行说明） | 同上 |
| `OS-BUILD-010`（`version.h` 必须经 `filechk` 生成，禁止裸 `echo` 覆盖） | 同上 |
| 内核发布串 `<kernel_version>-airymax.<airy_release>`（§8） | `docs/docs-linux/160-compatibility/03-upstream-tracking.md` |

## 检查项

| # | 检查项 | 规则 |
|---|--------|------|
| 1 | 清单 schema 合法（必填字段、四元组取值域 `0..255`、路径形态） | 本工具约定 |
| 2 | 版本文件存在（清单 `airymaxos_makefile` ↔ 内核树） | guard |
| 3 | 四元组取值与清单一致 | 本清单 |
| 4 | 版本文件仅含版本四元组 | `OS-STD-049` |
| 5 | 顶层 Makefile 经 `include` 引入版本文件 | `OS-BUILD-010` |
| 6 | `filechk_version.h` 注入全部 `AIRYMAXOS_*` 宏 | `OS-BUILD-010` |
| 7 | 契约快照与四元组一致 | 本清单 |
| 8 | 真实 `version.h` 与契约一致（给出 `--version-h` 时追加） | `OS-BUILD-010` |

清单 schema 不合法时仅报告第 1 项；版本文件缺失时报告至第 2 项并短路其余内容类
检查（避免在缺失数据上生成噪声结论）。

第 8 项为「构建产物级」校验：在真实执行 `make include/generated/uapi/linux/version.h`
后，以 `--version-h` 指向该产物，即可验证 filechk 真正把四元组注入到 C 宏，而非
仅停留在源码文本层面。

> **SSoT 注册一致性不在此重复实现**：规则 ID 与文档的登记一致性由门禁
> `OS-IRON-015`（`tools/validate-ssot.py`）统一保证，本工具只负责版本注入内容一致。

## 使用方法

```bash
# 人类可读完整报告（默认自动探测仓库根）
python3 tools/kbuild-version/kbuild_version.py

# CI 模式：精简输出，仅以退出码判定
python3 tools/kbuild-version/kbuild_version.py --check

# 指定仓库根 / 清单 / 真实构建产物（追加第 8 项）
python3 tools/kbuild-version/kbuild_version.py \
  --repo-root . \
  --manifest tools/kbuild-version/manifest.yaml \
  --version-h kernel/include/generated/uapi/linux/version.h --check
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部检查通过 |
| `1` | 一项或多项检查失败 |
| `2` | 用法 / 环境错误（仓库根/内核目录缺失、清单缺失、YAML 非法等） |

`--check` 输出 `KBUILD-VERSION: PASS (N/N checks)`，失败时逐项列出证据。

## 维护清单

**发布新的 AirymaxOS 版本时（对齐 `OS-BUILD-009`）**：依次改动

1. [manifest.yaml](manifest.yaml) 的 `version_tuple`；
2. [`kernel/Makefile.airymaxos`](../../kernel/Makefile.airymaxos) 的四个变量；
3. 契约快照 [contract/version_h.airy.txt](contract/version_h.airy.txt)（可先按四元组
   手写，再运行本工具确认与 `render_contract()` 推导结果一致）。

随后运行本工具确认无漂移。`AIRYMAXOS_VERSION_CODE = MAJOR * 256 + MINOR`，契约快照
中的 `VERSION_CODE` 一栏随之更新（如 `0.0.3 → 3`）。

四元组变更会触发 `version.h` 重生，进而导致全量重编——此为 `filechk` 增量语义的
预期行为，非缺陷。

> 禁止在 `Makefile.airymaxos` 中放置版本四元组以外的任何变量（`OS-STD-049`）：
> 特性开关一律走 Kconfig。本工具的第 4 项即静态保证该约束。

## CI 接入

- **管理仓 `airy-verify.yml`（`airy-static` job）**：以新增 step 调用 `--check`，
  遵守管理仓「每 workflow ≤ 2 job」的上限，不新增 job。
- **管理仓 `os-chk` 注册表**：以门禁 `OS-BUILD-KBUILD-VERSION`（P0 / category
  `build`）登记，由 `tools/os-chk/os_chk.py --check` 统一编排。
- **管理仓 `os-chk-verify.yml`**：`paths` 过滤器加入 `tools/kbuild-version/**`；
  安装 `tools/kbuild-version/requirements-dev.txt`；并将其 `tests/` 纳入 pytest 范围。

## 单元测试

```bash
pip install -r tools/kbuild-version/requirements-dev.txt
python3 -m pytest tools/kbuild-version/tests/ -q
```

测试以 `tmp_path` 构造受控合成仓库，逐项断言各检查器的通过/拒绝语义，并在真实
仓库上运行一例端到端回归。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
