<!-- SPDX-License-Identifier: GPL-2.0 -->

# abi-check — agentrt-linux syscall ABI 稳定性检查工具

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

验证 agentrt-linux 系统调用编号的 ABI 稳定性，对齐
**OS-IRON-001（用户空间 ABI 永不破坏）**。

工具从三个权威来源提取 syscall 编号并进行三方对比，同时与基线快照
对比确保编号不可变更性：

| 来源 | 文件 | 角色 |
|------|------|------|
| SSoT 文档 | `../docs/AirymaxOS/140-application-development/07-syscall-registry.md` | 唯一权威注册表 |
| UAPI 头文件 | `kernel/include/uapi/asm-generic/unistd.h` | 用户空间编号定义 |
| 内核入口表 | `kernel/arch/x86/entry/syscalls/syscall_64.tbl` | 内核注册编号 |

## 文件

```
tools/abi-check/
├── abi_check.py        # ABI 检查脚本（Python 3，仅依赖标准库）
├── abi_baseline.json   # ABI 基线快照（v1.0.1 权威定义）
└── README.md           # 本文件
```

## 检查项

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 三方一致性 | 4 核心 syscall 编号在 SSoT / unistd.h / syscall_64.tbl 三方一致 |
| 2 | 编号起点 | AIRY_SYS_BASE = 548，所有 airy syscall >= 548 |
| 3 | 范围无冲突 | 编号唯一且在 548-571 范围内，无重复 |
| 4 | __NR_syscalls | unistd.h 中 __NR_syscalls = 552 |
| 5 | x32 禁止区域 | 无 airy syscall 落入 512-547（x86_64 x32 历史遗留区域） |
| 6 | 基线一致 | 当前 ABI 与 abi_baseline.json 快照一致（编号不可变更） |

## 使用方法

### 本地运行（详细报告）

```bash
# 从 agentrt-linux 仓库根目录运行
python3 tools/abi-check/abi_check.py
```

输出示例：

```
========================================================================
agentrt-linux ABI Stability Check Report
OS-IRON-001: 用户空间 ABI 永不破坏
========================================================================

Sources:
  SSoT            : ../docs/AirymaxOS/.../07-syscall-registry.md
  unistd.h        : kernel/include/uapi/asm-generic/unistd.h
  syscall_64.tbl  : kernel/arch/x86/entry/syscalls/syscall_64.tbl
  baseline        : tools/abi-check/abi_baseline.json

SSoT version:     v1.0.1
Baseline version: v1.0.1

Checks: 6/6 passed, 0 failed

[+] PASS: 三方一致性 (SSoT / unistd.h / syscall_64.tbl)
[+] PASS: 编号起点为 548
[+] PASS: 编号范围 548-571 内无冲突
[+] PASS: __NR_syscalls 值正确
[+] PASS: 无 512-547 区域的 syscall (x32 禁止区域)
[+] PASS: 编号与基线快照一致

RESULT: PASS — all ABI checks passed (OS-IRON-001)
```

### CI 模式（仅退出码）

```bash
python3 tools/abi-check/abi_check.py --check
# 退出码: 0=通过, 1=失败, 2=输入/解析错误
```

### 跳过基线检查

```bash
python3 tools/abi-check/abi_check.py --no-baseline
```

### 指定自定义路径

```bash
python3 tools/abi-check/abi_check.py \
    --repo-root /path/to/agentrt-linux \
    --ssot-path /path/to/07-syscall-registry.md \
    --unistd-path /path/to/unistd.h \
    --tbl-path /path/to/syscall_64.tbl \
    --baseline /path/to/abi_baseline.json
```

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 所有 ABI 检查通过 |
| 1 | 一个或多个 ABI 检查失败 |
| 2 | 输入/解析错误（文件未找到、JSON 无效等） |

## CI 集成

在 GitHub Actions 中集成：

```yaml
- name: ABI stability check
  run: |
    python3 tools/abi-check/abi_check.py --check
```

`--check` 模式下，通过时输出 `ABI CHECK: PASS (6/6 checks)`，失败时
输出失败项摘要。CI 仅需检查退出码即可。

## 基线快照管理

`abi_baseline.json` 记录当前 MAJOR 版本的权威 ABI 定义。当 MAJOR
版本升级时，需更新基线快照：

1. 修改 `abi_baseline.json` 中的 syscalls、linux_base、nr_syscalls
2. 更新 version 与 date 字段
3. 提交时在 commit message 中说明 ABI 变更理由与 ADR 编号

在 MAJOR 版本内，基线快照不可修改（编号不可变更规则，§2.3）。

## 许可证

SPDX-License-Identifier: GPL-2.0
