<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# abi-check — agent-linux syscall ABI 稳定性检查工具

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

验证 agent-linux 系统调用编号的 ABI 稳定性，对齐
**OS-IRON-001（用户空间 ABI 永不破坏）**。

工具从四个权威来源提取 syscall 编号并进行四方对比，同时与基线快照
对比确保编号不可变更性：

| 来源 | 文件 | 角色 |
|------|------|------|
| SSoT 文档 | `../docs/docs-linux/140-application-development/07-syscall-registry.md` | 唯一权威注册表 |
| UAPI 头文件 | `kernel/include/uapi/asm-generic/unistd.h` | 用户空间编号定义 |
| 内核入口表 | `kernel/arch/x86/entry/syscalls/syscall_64.tbl` | 内核注册编号 |
| [SC] 契约头 | `kernel/include/uapi/linux/airymax/syscalls.h` | 共享契约宏定义 |
| 派生契约快照 | `tools/abi-check/contract/syscall_registry.snapshot.json` | SSoT 缺失时的只读回退源 |

SSoT 文档是唯一权威。派生契约快照由 SSoT 确定性导出（含源仓 commit
与 sha256 溯源），**不是第二权威**；当权威 SSoT 可得时，工具会以快照
做漂移比对，两者不一致即判定失败。GitHub 镜像未检出 `../docs` 子模块
时，工具以快照为 SSoT 来源运行，并在报告中显式标注该回退。

## 文件

```text
tools/abi-check/
├── abi_check.py          # ABI 检查脚本（Python 3，仅依赖标准库）
├── abi_baseline.json     # ABI 基线快照（v1.0.1 权威定义）
├── requirements-dev.txt  # 测试依赖（pytest）
├── contract/
│   └── syscall_registry.snapshot.json  # 派生契约快照（SSoT 导出）
├── tests/
│   ├── conftest.py       # 测试夹具（注入 sys.path）
│   └── test_abi_check.py # 单元测试
└── README.md             # 本文件
```

## 检查项

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 三方一致性 | 4 核心 syscall 编号在 SSoT / unistd.h / syscall_64.tbl 三方一致 |
| 2 | 编号起点 | AIRY_SYS_BASE = 548，所有 airy syscall >= 548 |
| 3 | 范围无冲突 | 编号唯一且在 548-571 范围内，无重复 |
| 4 | __NR_syscalls | unistd.h 中 __NR_syscalls = 572 |
| 5 | x32 禁止区域 | 无 airy syscall 落入 512-547（x86_64 x32 历史遗留区域） |
| 6 | [SC] 契约头一致 | syscalls.h 中 4 核心宏与预留标记（BASE/END/SLOTS_MAX）与 SSoT 及各源一致 |
| 7 | 预留槽登记一致 | 552-571 共 20 槽在 unistd.h / syscall_64.tbl 均按 `airy_reserved_N` 完整登记 |
| 8 | tbl ABI 列与入口点 | syscall_64.tbl 中 airy 条目 abi=common，核心入口为 `sys_airy_sys_*` |
| 9 | 基线一致 | 当前 ABI 与 abi_baseline.json 快照一致（编号不可变更） |
| 10 | SSoT 契约快照一致 | 派生契约快照与权威 SSoT 的契约内容一致；SSoT 缺失时以快照运行并标注溯源 |

## 使用方法

### 本地运行（详细报告）

```bash
# 从 agent-linux 仓库根目录运行
python3 tools/abi-check/abi_check.py
```

输出示例：

```text
========================================================================
agent-linux ABI Stability Check Report
OS-IRON-001: 用户空间 ABI 永不破坏
========================================================================

Sources:
  SSoT            : ../docs/docs-linux/.../07-syscall-registry.md
  snapshot        : tools/abi-check/contract/syscall_registry.snapshot.json
  unistd.h        : kernel/include/uapi/asm-generic/unistd.h
  syscall_64.tbl  : kernel/arch/x86/entry/syscalls/syscall_64.tbl
  [SC] syscalls.h : kernel/include/uapi/linux/airymax/syscalls.h
  baseline        : tools/abi-check/abi_baseline.json

SSoT version:     v1.0.1
Baseline version: v1.0.1

Checks: 10/10 passed, 0 failed

[+] PASS: 三方一致性 (SSoT / unistd.h / syscall_64.tbl)
[+] PASS: 编号起点为 548
[+] PASS: 编号范围 548-571 内无冲突
[+] PASS: __NR_syscalls 值正确
[+] PASS: 无 512-547 区域的 syscall (x32 禁止区域)
[+] PASS: [SC] 契约头一致 (syscalls.h)
[+] PASS: 预留槽登记一致 (552-571)
[+] PASS: syscall_64.tbl ABI 列与入口点正确
[+] PASS: 编号与基线快照一致
[+] PASS: SSoT 契约快照一致

RESULT: PASS — all ABI checks passed (OS-IRON-001)
```

### CI 模式（仅退出码）

```bash
python3 tools/abi-check/abi_check.py --check
# 退出码: 0=通过, 1=失败, 2=输入/解析错误
```

在 `../docs` 未检出的镜像环境中，工具以派生契约快照运行，并在输出首行
标注回退来源：

```text
[INFO] SSoT source: contract snapshot (../docs unavailable) — 源 docs-linux@08a96147e7e3, sha256=76148af4ce37…, doc_version=v1.0.1
ABI CHECK: PASS (10/10 checks)
```

### 跳过基线检查

```bash
python3 tools/abi-check/abi_check.py --no-baseline
```

### 指定自定义路径

```bash
python3 tools/abi-check/abi_check.py \
    --repo-root /path/to/agent-linux \
    --ssot-path /path/to/07-syscall-registry.md \
    --snapshot-path /path/to/syscall_registry.snapshot.json \
    --unistd-path /path/to/unistd.h \
    --tbl-path /path/to/syscall_64.tbl \
    --sc-header-path /path/to/syscalls.h \
    --baseline /path/to/abi_baseline.json
```

### 强制要求权威 SSoT

```bash
python3 tools/abi-check/abi_check.py --check --require-ssot
# 权威 SSoT 缺失时退出码 2，禁用快照回退（用于发布门禁）
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

`--check` 模式下，通过时输出 `ABI CHECK: PASS (10/10 checks)`，失败时
输出失败项摘要。CI 仅需检查退出码即可。

### 单元测试

```bash
pip install -r tools/abi-check/requirements-dev.txt
python3 -m pytest tools/abi-check/tests/ -q
```

## 基线快照管理

`abi_baseline.json` 记录当前 MAJOR 版本的权威 ABI 定义。当 MAJOR
版本升级时，需更新基线快照：

1. 修改 `abi_baseline.json` 中的 syscalls、linux_base、nr_syscalls
2. 更新 version 与 date 字段
3. 提交时在 commit message 中说明 ABI 变更理由与 ADR 编号

在 MAJOR 版本内，基线快照不可修改（编号不可变更规则，§2.3）。

## 契约快照管理

`contract/syscall_registry.snapshot.json` 是从权威 SSoT 文档确定性导出
的机器可读契约快照，供 CI 在未检出 `../docs` 的镜像环境中运行。

刷新流程（在检出 `../docs` 的开发机或管理仓上执行）：

```bash
# 从权威 SSoT 重新导出快照（含源仓 commit 与 sha256 溯源）
python3 tools/abi-check/abi_check.py --update-snapshot

# 重新运行检查；第 10 项会做漂移比对，不一致即失败
python3 tools/abi-check/abi_check.py --check
```

规则：

- 快照只能由 `--update-snapshot` 生成，禁止手工编辑。
- 权威 SSoT 可得的 CI 环境中，第 10 项检查会比对派生快照与文档的契约
  内容（linux_base / reserved_range / doc_version / syscall 宏与编号映射）；
  文档变更后若未刷新快照，门禁失败并提示运行 `--update-snapshot`。
- `--update-snapshot` 与 `--require-ssot` 在 SSoT 缺失时均以退出码 2
  失败关闭（fail-closed），确保快照不会被无权威源时误刷新。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
