<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# codegen — agent-linux 契约代码生成管道

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

将共享契约层（[SC]）的唯一权威契约源确定性渲染为 C UAPI 头与多语言
（Rust / Python / Go / TypeScript）SDK 绑定，消除手工样板，并保证契约源与
生成产物零漂移，对齐跨端共享模型 **IRON-9（同源且部分代码共享）**。

权威设计：《agent-linux 代码生成管道设计》（`06-codegen-pipeline.md`，
位于 docs-linux 的 `30-interfaces/`）。本目录是其在管理仓内的实现。

## 文件

```text
tools/codegen/
├── run_codegen.py        # 统一入口（`--check` 为 CI 零漂移门禁）
├── cap_gen.py            # Capability ID 生成器
├── bitfield_gen.py       # 位域结构生成器
├── ipc_gen.py            # IPC 消息头生成器
├── syscall_gen.py        # syscall 多语言绑定生成器
├── codegen_io.py         # 共享 I/O 脚手架
├── codegen_types.py      # 共享类型与依赖序（PIPELINE_ORDER）定义
├── templates/            # Jinja2 输出模板
├── tests/                # pytest 单元测试套件
├── Makefile              # 本地驱动（all / cap / bitfield / ipc / syscall / check / test / clean）
├── requirements.txt      # 运行时依赖锁定
├── requirements-dev.txt  # 测试依赖（引用 requirements.txt）
└── README.md             # 本文件
```

## 契约源与生成器映射

每类契约只有一个权威输入源，禁止就地重定义（§9.1）。

| 契约源 | 生成器 | 输入文件 | 生成产物 |
|--------|--------|----------|----------|
| 系统调用（R-01） | `syscall_gen.py` | `kernel/include/uapi/linux/airymax/syscall.xml` | C `syscall_gen.h` + `syscall_64.tbl`（Airymax 段）+ Rust/Python/Go/TS 绑定 |
| 位域结构（R-02） | `bitfield_gen.py` | `kernel/include/uapi/linux/airymax/structures_32.bf` / `structures_64.bf` | C `structures_32_gen.h` / `structures_64_gen.h` |
| IPC 消息头 | `ipc_gen.py` | `docs-linux/50-engineering-standards/20-contracts/contracts.md` §2 | C `ipc.h` + Rust/Python/Go/TS 绑定 |
| Capability ID | `cap_gen.py` | `security/include/airy_cap_ids.def` | C `security_types.h` + Rust/Python 绑定（**不生成 LSM 钩子 ID**，钩子经 `LSM_HOOK_INIT` 宏名静态注册） |

## 生成顺序与依赖

依赖序由 [`codegen_types.py`](codegen_types.py) 的 `PIPELINE_ORDER` 唯一定义，
统一入口 [`run_codegen.py`](run_codegen.py) 只做编排，不承载生成逻辑：

```text
cap_gen  →  bitfield_gen  ∥  ipc_gen  →  syscall_gen
```

1. `cap_gen` 最先运行——`syscall.xml` 的 `<capability_required>` 引用其产出的符号。
2. `bitfield_gen` 与 `ipc_gen` 居中并列——IPC 消息头位域布局来自 `.bf`，结构体定义来自 `contracts.md`。
3. `syscall_gen` 最后运行——依赖 capability 符号校验与 IPC 消息头类型。

任一阶段返回非零即短路透传，保证 CI 能在契约源或生成产物漂移时阻断合并。

## 使用方法

### 生成全部产物（默认）

```bash
# 从 agent-linux 仓库根目录运行
make -C tools/codegen
# 或直接调用统一入口
python3 tools/codegen/run_codegen.py
```

### 零漂移校验（CI 门禁）

```bash
python3 tools/codegen/run_codegen.py --check
# 退出码: 0=零漂移, 非零=契约源或产物漂移
```

`--check` 会逐阶段对比「重新渲染的内容」与「已提交文件」，并对
`syscall_64.tbl` 的 Airymax 段执行 `check_tbl` 一致性校验；漂移即返回非零。

### 运行单元测试

```bash
make -C tools/codegen test
# 等价于
python3 -m pytest tools/codegen/tests/ -q
```

测试套件覆盖 4 个生成器、管道编排与 I/O 脚手架：

- `test_cap_gen.py` / `test_bitfield_gen.py` / `test_ipc_gen.py` / `test_syscall_gen.py`
- `test_run_codegen.py`（编排与短路）
- `test_codegen_io.py` / `test_codegen_types.py`（共享脚手架）

### 指定仓库根

```bash
python3 tools/codegen/run_codegen.py --repo-root /path/to/agent-linux
```

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 生成或校验成功（零漂移） |
| 非零 | 生成器报错，或 `--check` 检出内容漂移 |

## CI 集成

[`.github/workflows/codegen-verify.yml`](../../.github/workflows/codegen-verify.yml)
在契约源或生成产物变更时触发（`paths` 含 `tools/codegen/**`、`sdk/**`、
`kernel`、`security`），执行：

```bash
python3 -m pytest tools/codegen/tests/ -q
python3 tools/codegen/run_codegen.py --check
```

diff 非空或生成器报错时 job 失败，PR 不得合并（§7.2、§9.3）。

## 依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.12 | 运行时 |
| PyYAML | >= 6.0,<7.0 | 解析契约源的 YAML 前置元数据 |
| Jinja2 | >= 3.1,<4.0 | 渲染多语言绑定模板 |
| pytest | >= 8.0,<9.0 | 单元测试（仅开发/CI） |

安装：

```bash
pip install -r tools/codegen/requirements.txt       # 运行时
pip install -r tools/codegen/requirements-dev.txt   # 测试
```

## 一致性保证

| 机制 | 阶段 | 目标 |
|------|------|------|
| 契约源唯一性 | 设计期 | 每类契约只有一个权威输入，禁止就地重定义 |
| 跨契约引用校验 | 生成期 | `syscall.xml` 引用的 capability 符号必须存在 |
| 幂等生成 | 生成期 | 同一输入多次生成，输出逐字节相同（`trim_blocks` + `lstrip_blocks`，无时间戳） |
| `@generated` 标记 | 提交期 | 生成文件头部标注来源，checkpatch 拒绝手工修改 |
| CI diff 检查 | CI 期 | 生成产物与已提交文件 diff 必须为空 |
| 原子提交约束 | CI 期 | 契约源变更与生成产物更新必须同一 commit |

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
