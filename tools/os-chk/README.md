<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# os-chk — AirymaxOS 统一门禁编排器（OS-CHK）

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

把分散在 `tools/` 下的各检查工具编排为统一门禁，按
`05-development-process.md` Part III 的 **OS-CHK 编号体系（§6）** 与
**检查报告格式（§5.3）** 输出报告，并按严重级（P0/P1/P2）判定是否阻断。

编排器自身不实现任何检查逻辑——它只做「注册表解析 → 调度 → 汇总 →
门禁判定」，检查本体由既有执行器承担，从而避免逻辑重复与长期漂移。

权威依据：

| 内容 | 出处 |
|------|------|
| OS-CHK 编号体系（§6.1/6.2/6.3） | `docs/docs-linux/50-engineering-standards/05-development-process.md` |
| 检查报告格式（§5.3） | 同上 |
| 门禁定义 | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` |

## 文件

```text
tools/os-chk/
├── os_chk.py             # 统一门禁编排器（Python 3，依赖 pyyaml）
├── gates.yaml            # 门禁注册表（唯一事实源，只登记真实可执行器）
├── requirements-dev.txt  # 运行时与测试依赖（pyyaml + pytest）
├── tests/
│   ├── conftest.py       # 测试夹具（注入 sys.path）
│   └── test_os_chk.py    # 单元测试
└── README.md             # 本文件
```

## 注册表（gates.yaml）

注册表是门禁的**唯一事实源**。字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 门禁编号（`OS-CHK-*` / `OS-IRON-*` / 工具门禁标识），全局唯一 |
| `name` | 是 | 门禁名称（简短，用于报告） |
| `severity` | 是 | 严重级：`P0` / `P1` / `P2` |
| `category` | 否 | 类别：`doc` / `code` / `iron` / `build` |
| `authority` | 否 | 规则权威出处（文档路径 §章节） |
| `executor.argv` | 是 | 执行器命令行（相对仓库根，`argv[0]` 需在 PATH 中） |
| `timeout_s` | 否 | 超时秒数（默认 600） |

**严格 schema**：门禁层与 `executor` 层出现未登记字段将被判为非法，
以防字段错位（例如把 `timeout_s` 误缩进到 `executor` 下）被静默忽略。

**铁律**：注册表只登记「存在真实可执行器」的门禁；需要新增门禁时，
先落地其执行器工具，再在此登记。

当前登记门禁（全部 P0）：

| 门禁 | 名称 | 执行器 |
|------|------|--------|
| OS-CHK-DOC-01 | Copyright 头部 | `tools/check-copyright.py` |
| OS-CHK-DOC-04 | [SC] 头文件清单一致性 | `tools/sc-tri-compile/sc_tri_compile.py` |
| OS-IRON-001 | syscall ABI 稳定性 | `tools/abi-check/abi_check.py` |
| OS-IRON-015 | SSoT 规则 ID 注册一致性 | `tools/validate-ssot.py` |
| OS-IRON-009-CODEGEN | codegen 生成产物零漂移 | `tools/codegen/run_codegen.py` |
| OS-IRON-009-DSL | 策略 DSL 决策表零漂移 | `tools/dsl/dsl_compile.py` |

## 使用方法

### 本地运行（详细报告）

```bash
# 从 agent-linux 仓库根目录运行
python3 tools/os-chk/os_chk.py
```

输出示例（§5.3 格式）：

```text
========================================================================
AirymaxOS OS-CHK 门禁报告
注册表版本: 1.0.0
========================================================================

[2026-11-17 09:00:00] [  OK  ] OS-CHK-DOC-01 Copyright 头部: OK: 所有受检 .md 文件的第 1 行均含版权头
[2026-11-17 09:00:00] [  OK  ] OS-CHK-DOC-04 [SC] 头文件清单一致性: SC TRI-COMPILE: PASS (4/4 checks)
[2026-11-17 09:00:01] [  OK  ] OS-IRON-001 syscall ABI 稳定性: ABI CHECK: PASS (9/9 checks)
[2026-11-17 09:00:01] [  OK  ] OS-IRON-015 SSoT 规则 ID 注册一致性: RESULT: PASS — all rule IDs registered
[2026-11-17 09:00:01] [  OK  ] OS-IRON-009-CODEGEN codegen 生成产物零漂移: [codegen] all 4 generators verified successfully
[2026-11-17 09:00:02] [  OK  ] OS-IRON-009-DSL 策略 DSL 决策表零漂移: OK: dsl_compile outputs match committed files (1 files)

------------------------------------------------------------------------
RESULT: PASS — 全部 6 条门禁通过（1.3s）
------------------------------------------------------------------------
```

### CI 模式（仅退出码）

```bash
python3 tools/os-chk/os_chk.py --check
# 输出: OS-CHK: PASS (6/6 gates, 1.3s)
# 退出码: 0=通过, 1=阻断, 2=用法/环境错误
```

### 列出 / 选择性运行

```bash
python3 tools/os-chk/os_chk.py --list                 # 列出注册表门禁
python3 tools/os-chk/os_chk.py --gate OS-IRON-001     # 仅运行指定门禁（可重复）
python3 tools/os-chk/os_chk.py --verbose              # 未通过门禁打印完整输出
python3 tools/os-chk/os_chk.py --json report.json     # 输出 JSON 报告
```

### 阻断阈值

`--fail-on` 决定「哪些严重级未通过时阻断」，默认 `P1`：

| 取值 | 阻断范围 | 适用场景 |
|------|----------|----------|
| `P0` | 仅 P0 | 仅核心铁律阻断 |
| `P1`（默认） | P0 + P1 | CI 强制阻断（对齐 05-development-process.md） |
| `P2` | P0 + P1 + P2 | 全量严格 |
| `none` | 不阻断 | 仅出报告，永不失败 |

> 未通过（`FAIL` 执行器非零退出 / `ERROR` 执行器无法启动或超时）的
> 门禁，其严重级不高于阈值时即构成阻断。

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--repo-root PATH` | 自动探测 | 仓库根（检出 `.git`，否则回退脚本位置） |
| `--registry PATH` | `tools/os-chk/gates.yaml` | 门禁注册表 |
| `--gate ID` | 全部 | 仅运行指定门禁（可重复） |
| `--fail-on {P0,P1,P2,none}` | `P1` | 阻断阈值 |
| `--timeout SECONDS` | 600 | 未显式声明超时的门禁所用默认超时 |
| `--json PATH` | — | 输出 JSON 报告 |
| `--list` | — | 仅列出注册表门禁 |
| `--check` | — | CI 模式：精简输出 |
| `--verbose` | — | 打印未通过门禁的完整 stdout/stderr |

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 通过（无达到阻断阈值的未通过门禁） |
| 1 | 阻断（存在达到阈值的未通过门禁） |
| 2 | 用法/环境错误（注册表缺失、schema 非法、参数错误等） |

## CI 集成

`os-chk-verify.yml` 在 `pull_request` 与 `main`/`develop` 推送时运行
单元测试与全量门禁：

```yaml
- name: Run OS-CHK orchestrator
  run: python3 tools/os-chk/os_chk.py --check
```

也可在既有流水线中作为聚合门禁单步调用（推荐），避免逐工具重复调度。

### 单元测试

```bash
pip install -r tools/os-chk/requirements-dev.txt
python3 -m pytest tools/os-chk/tests/ -q
```

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
