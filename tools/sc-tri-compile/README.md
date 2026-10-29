<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# sc-tri-compile — agent-linux [SC] 契约头多路编译与布局校验

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

验证 `[SC]` 共享契约层头文件在常规三路与 `[DSL]` 降级两路类型桥接路径下
均可编译，且定宽类型大小与结构体内存布局在各路下完全一致，并对 syscall
ABI 的三份权威来源（`syscalls.h` ↔ `syscall.xml` ↔ `syscall_gen.h`）做符号
名与编号的双向一致性校验，对齐
**OS-IRON-014（[SC] 共享契约头单一权威）** 与
`docs-linux/50-engineering-standards/11-sc-header-type-bridging.md`。

`[SC]` 头物理宿主为 `kernel/include/uapi/linux/airymax/`，被内核态、
Linux 用户态、第三方（macOS/Windows 等非 Linux 宿主）三方共同消费。任一
路径编译失败或布局漂移都会静默破坏 ABI，因此本工具把「多路编译 +
`sizeof` + `offsetof` 一致」固化为可复现、可进 CI 的门禁；`[DSL]` 降级
两路另以 `AIRY_SC_FALLBACK` 固定 `__KERNEL__` / `__linux__` 两分支契约
（`docs-linux/10-architecture/11-degraded-survival-layer.md` §2.2）。

## 文件

```text
tools/sc-tri-compile/
├── sc_tri_compile.py     # 多路编译与布局校验脚本（运行期仅依赖标准库）
├── requirements-dev.txt  # 测试依赖（pytest）
├── tests/
│   ├── conftest.py
│   └── test_sc_tri_compile.py
└── README.md             # 本文件
```

## 多路编译模型

| # | 路径 | 编译命令 | 消费方 |
|---|------|----------|--------|
| 1 | 内核态 | `gcc -std=gnu11 -D__KERNEL__ -D__linux__` | Linux 内核（`uapi_compat.h` 走 `__KERNEL__` 分支） |
| 2 | Linux 用户态 | `gcc -std=gnu11 -D__linux__` | Linux userspace（走 `__linux__` 分支） |
| 3 | 第三方 | `gcc -std=c99 -U__linux__` | macOS/Windows 等非 Linux 宿主（走 `#else` 降级分支） |
| 4 | `[DSL]` 内核态 | `#1 + -DAIRY_SC_FALLBACK -Wno-error=cpp` | 降级生存层，仅 `__KERNEL__` 分支 |
| 5 | `[DSL]` Linux 用户态 | `#2 + -DAIRY_SC_FALLBACK -Wno-error=cpp` | 降级生存层，仅 `__linux__` 分支 |

`[DSL]` 两路按 `11-degraded-survival-layer.md` §2.2 固定两分支契约；所有
`[SC]` 头在降级模式下按 §2.1 必须发出 `#warning`，故这两路使用
`-Wno-error=cpp` 仅豁免 `#warning`，其余告警仍按 `-Werror` 升级。

include 搜索顺序严格镜像 Kbuild：`-Iinclude` 先于 `-Iinclude/uapi`，使
`<linux/stddef.h>` 解析为内核内部头（在 `-nostdinc` 语义下唯一的
`offsetof` 提供者），而非 UAPI stub。

## 检查项

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 五路编译通过 | `kernel/include/uapi/linux/airymax/` 下全部 `[SC]` 头文件在常规三路与 `[DSL]` 降级两路下 `-fsyntax-only -Werror` 零错误 |
| 2 | 类型大小五路一致 | `__u8/__u16/__u32/__u64/__s8/__s16/__s32/__s64` 在各路下 `sizeof` 一致 |
| 3 | 字段偏移五路一致 | 契约结构体（`airy_ipc_msg_hdr`、`airy_log_record`、`airy_task_desc`、`bpf_struct_ops_common_val`、`airy_struct_ops_value`）的 `sizeof` 与关键字段 `offsetof` 各路一致 |
| 4 | `[DSL]` 降级契约 | `dsl-*` 路径探针报告 `AIRY_DSL_UAPI_BRANCHES == 2`，常规路径报告降级层未激活 |
| 5 | syscall ABI 三源一致 | 契约头 `syscalls.h`、R-01 SSoT `syscall.xml`、生成产物 `syscall_gen.h` 三者的核心 syscall 符号名（`AIRY_SYS_*` / `__NR_airy_sys_*` / `AIRY_SYS_IDX_*`）与编号（`548-571` 号段、`RESERVED_*` 边界、槽位算术）双向一致 |

第 2、3、4 项通过编译并运行一个临时探针程序（probe）采集各路实测值后逐
项比对；探针编译刻意不加 `-Werror`——内核路径混入 glibc `<stdio.h>` 会
产生良性重定义告警，但生成的可执行文件正确。

第 5 项为纯文本解析（不依赖 `gcc`）：C 宏正则排除函数式宏与 token 别名，
XML 结构解析覆盖根元素、`<meta>` 前缀、`<syscall>` 条目与 `<reserved>`
区间的畸形路径，任一来源漂移即判失败。

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
OS-IRON-014: [SC] 共享契约头多路编译与二进制布局一致
========================================================================

Sources:
  headers-dir   : kernel/include/uapi/linux/airymax
  kernel-dir    : kernel
  ...

Compiler:  gcc
Headers:   14 个 [SC] 头文件

------------------------------------------------------------------------
Checks: 5/5 passed, 0 failed
------------------------------------------------------------------------

[+] PASS: 五路编译通过 (常规三路 + [DSL] 降级两路)
      14 个头文件 × 5 路径 = 70 次编译全部通过

[+] PASS: 类型大小五路一致 (__u8..__s64)
      8 个定宽类型各路径大小一致

[+] PASS: 结构体字段偏移五路一致
      5 个结构体、27 个字段偏移各路径一致

[+] PASS: [DSL] 降级路径契约 (__KERNEL__ / __linux__ 两路)
      2 条降级路径均桥接到 2 路

[+] PASS: syscall ABI 三源一致 (syscalls.h ↔ syscall.xml ↔ syscall_gen.h)
      4 个核心 syscall + 20 个预留槽，号段 548-571 符号名与编号三源一致

------------------------------------------------------------------------
RESULT: PASS — all [SC] tri-compile checks passed (OS-IRON-014)
------------------------------------------------------------------------
```

### CI 模式（仅退出码）

```bash
python3 tools/sc-tri-compile/sc_tri_compile.py --check
# 退出码: 0=通过, 1=失败, 2=输入/环境错误
```

`--check` 模式下，通过时输出 `SC TRI-COMPILE: PASS (5/5 checks)`，失败时
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

测试覆盖常量矩阵（常规三路 + `[DSL]` 降级两路编译路径、定宽类型大小、契约
结构体布局）、Kbuild include 顺序镜像、探针源码生成与解析、一致性判定、
`[DSL]` 两路桥接契约、syscall ABI 三源解析（C 宏正则、XML 结构）、符号名
与编号双向一致校验，以及 CLI 退出码契约。真实仓库级多路编译校验需 `gcc`
与完整 kernel 子树，缺失时自动跳过该组用例（`pytest.mark.skipif`）。

## CI 集成

`os-chk-verify.yml` 在 `tools/**` 或所监听 gitlink 变更时跑完整 Python 套件，
本工具的单测与多路编译门禁都在其中：

```yaml
- name: "Run sc-tri-compile unit tests (pytest)"
  run: python3 -m pytest tools/sc-tri-compile/tests/ -q
- name: "[SC] tri-compile & layout check (OS-IRON-014)"
  run: python3 tools/sc-tri-compile/sc_tri_compile.py --check
```

校验脚本运行期不需要额外 Python 依赖（仅标准库）；仅供测试使用的 pytest
依赖声明在 `requirements-dev.txt`，CI 经 `ci/gates/python-verify.sh` 的 oschk
剖面统一装齐。

## 与设计文档的关系

- 权威设计：`docs-linux/50-engineering-standards/11-sc-header-type-bridging.md`
  （§3.3 二进制布局一致性、§5.1 编译矩阵、§5.3 校验清单）。
- `[DSL]` 降级契约：`docs-linux/10-architecture/11-degraded-survival-layer.md`
  （§2.1 三原则、§2.2 两路桥接职责）。
- 类型桥接实现：`kernel/include/uapi/linux/airymax/uapi_compat.h`
  （`__KERNEL__` / `__linux__` / `#else` 三分支）。
- syscall ABI 三源：`kernel/include/uapi/linux/airymax/syscalls.h`（契约头
  SSoT 形式）、`syscall.xml`（R-01 SSoT）、`syscall_gen.h`（`@generated`
  生成产物），由 `os-chk-verify.yml` 的 OS-CHK-DOC-04 门禁收口（L-04 /
  D-04）。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
