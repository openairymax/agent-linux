<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# sc-tri-compile — agent-linux [SC] 契约头三路编译与布局校验

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

验证 `[SC]` 共享契约层头文件在三种类型桥接路径下均可编译，且定宽
类型大小与结构体内存布局在三路下完全一致，对齐
**OS-IRON-014（[SC] 共享契约头单一权威）** 与
`docs-linux/50-engineering-standards/11-sc-header-type-bridging.md`。

`[SC]` 头物理宿主为 `kernel/include/uapi/linux/airymax/`，被内核态、
Linux 用户态、第三方（macOS/Windows 等非 Linux 宿主）三方共同消费。任
一路径编译失败或布局漂移都会静默破坏 ABI，因此本工具把「三路编译 +
`sizeof` + `offsetof` 一致」固化为可复现、可进 CI 的门禁。

## 文件

```text
tools/sc-tri-compile/
├── sc_tri_compile.py     # 三路编译与布局校验脚本（运行期仅依赖标准库）
├── requirements-dev.txt  # 测试依赖（pytest）
├── tests/
│   ├── conftest.py
│   └── test_sc_tri_compile.py
└── README.md             # 本文件
```

## 三路编译模型

| # | 路径 | 编译命令 | 消费方 |
|---|------|----------|--------|
| 1 | 内核态 | `gcc -std=gnu11 -D__KERNEL__ -D__linux__` | Linux 内核（`uapi_compat.h` 走 `__KERNEL__` 分支） |
| 2 | Linux 用户态 | `gcc -std=gnu11 -D__linux__` | Linux userspace（走 `__linux__` 分支） |
| 3 | 第三方 | `gcc -std=c99 -U__linux__` | macOS/Windows 等非 Linux 宿主（走 `#else` 降级分支） |

include 搜索顺序严格镜像 Kbuild：`-Iinclude` 先于 `-Iinclude/uapi`，使
`<linux/stddef.h>` 解析为内核内部头（在 `-nostdinc` 语义下唯一的
`offsetof` 提供者），而非 UAPI stub。

## 检查项

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 三路编译通过 | `kernel/include/uapi/linux/airymax/` 下全部 `[SC]` 头文件在内核态 / Linux 用户态 / 第三方三路下 `-fsyntax-only -Werror` 零错误 |
| 2 | 类型大小三路一致 | `__u8/__u16/__u32/__u64/__s8/__s16/__s32/__s64` 在三路下 `sizeof` 一致 |
| 3 | 字段偏移三路一致 | 契约结构体（`airy_ipc_msg_hdr`、`airy_log_record`、`airy_task_desc`、`bpf_struct_ops_common_val`、`airy_struct_ops_value`）的 `sizeof` 与关键字段 `offsetof` 三路一致 |

第 2、3 项通过编译并运行一个临时探针程序（probe）采集三路实测值后逐项
比对；探针编译刻意不加 `-Werror`——内核路径混入 glibc `<stdio.h>` 会
产生良性重定义告警，但生成的可执行文件正确。

## 使用方法

### 本地运行（详细报告）

```bash
# 从 agent-linux 仓库根目录运行
python3 tools/sc-tri-compile/sc_tri_compile.py
```

输出示例：

```text
========================================================================
agent-linux [SC] Tri-Compile & Layout Check Report
OS-IRON-014: [SC] 共享契约头三路编译与二进制布局一致
========================================================================

Sources:
  headers-dir   : kernel/include/uapi/linux/airymax
  kernel-dir    : kernel
  ...

Compiler:  gcc
Headers:   14 个 [SC] 头文件

------------------------------------------------------------------------
Checks: 3/3 passed, 0 failed
------------------------------------------------------------------------

[+] PASS: 三路编译通过 (内核态 / Linux 用户态 / 第三方)
      14 个头文件 × 3 路径 = 42 次编译全部通过

[+] PASS: 类型大小三路一致 (__u8..__s64)
      8 个定宽类型三路大小一致

[+] PASS: 结构体字段偏移三路一致
      5 个结构体、27 个字段偏移三路一致

------------------------------------------------------------------------
RESULT: PASS — all [SC] tri-compile checks passed (OS-IRON-014)
------------------------------------------------------------------------
```

### CI 模式（仅退出码）

```bash
python3 tools/sc-tri-compile/sc_tri_compile.py --check
# 退出码: 0=通过, 1=失败, 2=输入/环境错误
```

`--check` 模式下，通过时输出 `SC TRI-COMPILE: PASS (3/3 checks)`，失败时
输出失败项摘要。CI 仅需检查退出码即可。

### 指定自定义路径 / 编译器

```bash
python3 tools/sc-tri-compile/sc_tri_compile.py \
    --repo-root /path/to/agent-linux \
    --headers-dir /path/to/kernel/include/uapi/linux/airymax \
    --kernel-dir /path/to/kernel \
    --cc gcc
```

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 所有检查通过 |
| 1 | 一个或多个检查失败（编译错误 / 大小或偏移不一致） |
| 2 | 输入/环境错误（目录未找到、编译器无法执行等） |

## 单元测试

```bash
python3 -m pytest tools/sc-tri-compile/tests/ -q
```

测试覆盖常量矩阵（三路编译路径、定宽类型大小、契约结构体布局）、Kbuild
include 顺序镜像、探针源码生成与解析、一致性判定，以及 CLI 退出码契约。
真实仓库级三路编译校验需 `gcc` 与完整 kernel 子树，缺失时自动跳过该组用
例（`pytest.mark.skipif`）。

## CI 集成

`sc-dual-ci.yml` 的 `sc-validate` job 在 `[SC]` 头文件或本工具变更时先跑单
元测试，再执行三路编译门禁：

```yaml
- name: "Run sc-tri-compile unit tests (pytest)"
  run: python3 -m pytest tools/sc-tri-compile/tests/ -q
- name: "[SC] tri-compile & layout check (OS-IRON-014)"
  run: python3 tools/sc-tri-compile/sc_tri_compile.py --check
```

校验脚本运行期不需要额外 Python 依赖（仅标准库）；仅供测试使用的 pytest
依赖声明在 `requirements-dev.txt`，CI 通过 `pip install -r
tools/sc-tri-compile/requirements-dev.txt` 安装。

## 与设计文档的关系

- 权威设计：`docs-linux/50-engineering-standards/11-sc-header-type-bridging.md`
  （§3.3 二进制布局一致性、§5.1 三路编译矩阵、§5.3 校验清单）。
- 类型桥接实现：`kernel/include/uapi/linux/airymax/uapi_compat.h`
  （`__KERNEL__` / `__linux__` / `#else` 三分支）。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
