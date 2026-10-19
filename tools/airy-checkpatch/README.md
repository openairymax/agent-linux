<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# airy-checkpatch — AirymaxOS 专属 checkpatch 扩展规则

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

上游 `scripts/checkpatch.pl` 负责通用内核风格；本工具在其之外补充
**AirymaxOS 子系统架构约束**检查（`AIRY-CHECKPATCH-001` ~ `011`），
覆盖日志接口、命名前缀、并发内存序、IPC 入口、LSM 注册、DMA 使用、
配置项黑白名单，以及文件头规范。

规则逻辑本体唯一落在 kernel 子模块的 Perl 引擎中：

```text
kernel/scripts/airy_checkpatch_ext.pl
```

本目录**不实现任何规则**，只承载该引擎的集成测试与文档，避免规则逻辑
在两处漂移（SSoT）。

权威依据：

| 内容 | 出处 |
|------|------|
| `AIRY-CHECKPATCH-001~010`（OS-TEST-066 / OS-KER-120） | `docs/docs-linux/80-testing/05-static-analysis.md` §7.2 |
| `AIRY-CHECKPATCH-011` 文件头规范（OS-STD-STY-007） | `docs/docs-linux/50-engineering-standards/01-coding-standards.md` §8.5.3 |
| PR 阶段静态分析门禁 | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` |

## 规则集

| 规则 | 级别 | 约束 |
|------|------|------|
| `AIRY-CHECKPATCH-001` | error | 使用 `pr_*()` 而非 `printk()` |
| `AIRY-CHECKPATCH-002` | error | AirymaxOS 函数须以 `airy_`/`stc_` 前缀命名 |
| `AIRY-CHECKPATCH-003` | error | `airy_agent_state` 访问须用 `READ_ONCE`/`WRITE_ONCE` |
| `AIRY-CHECKPATCH-004` | error | `airy_ipc_send()` 返回值须被检查 |
| `AIRY-CHECKPATCH-005` | warning | `airy_cap_check()` 须显式处理 `-EPERM` |
| `AIRY-CHECKPATCH-006` | error | 禁止在 `airy_*` 函数内直接调用 `printk()` |
| `AIRY-CHECKPATCH-007` | error | LSM 注册须使用 `security_hook_list` |
| `AIRY-CHECKPATCH-008` | error | `airy_ipc`/`airy_mem` 路径禁止 `dma_alloc_coherent()` |
| `AIRY-CHECKPATCH-009` | error | AirymaxOS 路径禁止 `CONFIG_BPF_LSM=y` |
| `AIRY-CHECKPATCH-010` | error | AirymaxOS 路径禁止 `CONFIG_SCHED_EXT=y` |
| `AIRY-CHECKPATCH-011` | error | 文件头：SPDX 首行 + 前导注释区 ≤ 20 行 |

**error 阻断合入；warning 仅告警，不阻断**（退出码只由 error 决定）。

## 使用方法

```bash
# 在 kernel 子模块根目录运行；省略路径则扫描 AirymaxOS 全部落点
LC_ALL=C perl kernel/scripts/airy_checkpatch_ext.pl

# 仅扫描指定路径
LC_ALL=C perl kernel/scripts/airy_checkpatch_ext.pl security/airy

# 查看帮助（含全部规则与退出码说明）
perl kernel/scripts/airy_checkpatch_ext.pl --help

# 输出规则注册表（TSV：id / 级别 / 摘要），供门禁与文档核对
perl kernel/scripts/airy_checkpatch_ext.pl --list-rules
```

规则集由引擎内的单一注册表维护，`--help` 与 `--list-rules` 均由其派生。

默认扫描目录（相对 kernel 根）：

```text
security/airy  kernel/superv  kernel/syscalls  kernel/corekern
kernel/log     kernel/ipc     include/airymax  include/uapi/linux/airymax
```

并自动追加 `arch/*/configs/airy*_defconfig`。

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 无 `error` 级违规（允许存在 `warning`） |
| `1` | 存在 `error` 级违规 |
| `2` | 用法或 IO 错误 |

输出格式：`<相对路径>:<行号>: <ERROR|WARNING> <规则号>: <说明>`，
末行为汇总 `airy_checkpatch_ext: N files checked, N errors, N warnings`。

## CI 接入

- **管理仓 `ci-airy.yml`（`airy-static` job）**：以新增 step 调用引擎，
  遵守管理仓「每 workflow ≤ 2 job」的上限，不新增 job。
- **管理仓 `os-chk` 注册表**：以门禁 `OS-TEST-066`（P0 / category `code`）
  登记，由 `tools/os-chk/os_chk.py --check` 统一编排。

## 单元测试

```bash
pip install -r tools/airy-checkpatch/requirements-dev.txt
python3 -m pytest tools/airy-checkpatch/tests/ -q
```

测试以受控输入驱动真实引擎，逐条断言规则命中与严重级语义；`perl`
不可用时整套跳过。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
