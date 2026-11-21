<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# dsl — AirymaxOS 策略 DSL（.adl）编译器

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

把 `.adl` 策略契约源编译为纯 C 决策表头，供内核侧 Airy LSM 直接包含。
规则的语义、verdict 与故障码来自 [SC] 契约，编译器只做确定性翻译：
同一份 `.adl` 在任何机器上生成逐字节一致的 `.h`。

口径见 `../docs/docs-linux/30-interfaces/06-codegen-pipeline.md` §9.3。
生成物提交入库，`--check` 校验「重新生成的结果与已提交文件零差异」，
因此策略漂移在 CI 判红，而不是等到内核行为变化才发现。

## 文件

```text
tools/dsl/
├── dsl_compile.py        # 编译编排与零漂移 CLI（词法/语法/语义/代码生成的组合）
├── dsl_lexer.py          # 词法与记号流
├── dsl_parser.py         # 递归下降解析，产出 AST
├── dsl_semantics.py      # 语义校验并降为 IR
├── dsl_types.py          # 编译期数据结构（AST/IR 节点与错误类型）
├── dsl_codegen.py        # IR → C 决策表（Jinja2 模板渲染）
├── Makefile              # all / check / test / clean 四个目标
├── templates/
│   └── dsl_decision_table.h.j2  # 决策表模板
├── examples/
│   ├── security_policy.adl      # 参考 LSM 策略（端到端样例）
│   └── security_policy_tables.h # 生成物，提交入库
├── tests/                # lexer/parser/semantics/codegen/compile 单元测试
└── README.md             # 本文件
```

## 用法

```sh
make -C tools/dsl all     # 重新生成 security_policy_tables.h
make -C tools/dsl check   # 零漂移校验（CI 使用）
make -C tools/dsl test    # 单元测试
make -C tools/dsl clean   # 删除生成物（提交前需重新生成）
```

直接调用入口：

```sh
python3 tools/dsl/dsl_compile.py            # 生成全部产物
python3 tools/dsl/dsl_compile.py --check    # 只校验，不写盘
python3 tools/dsl/dsl_compile.py --repo-root DIR
```

## 契约与边界

- **生成物入库**：`examples/security_policy_tables.h` 是交付面的一部分，
  改 `.adl` 或模板必须同批提交重新生成的结果，否则 `check` 失败。
- **单源产物登记**：产物清单由 `dsl_compile.py` 的 `CONTRACTS` 登记，
  写入统一走 `tools/codegen/codegen_io.py`；不在别处再写一份路径表。
- **依赖**：仅 Python 标准库与 Jinja2（模板渲染），测试依赖 pytest。
- **不写内核树**：编译器只读 `.adl`、只写登记过的生成物，
  内核侧改动由内核仓自己的补丁流程负责。
