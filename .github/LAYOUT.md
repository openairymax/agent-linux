# `.github/` — GitHub 自动化与模板

> [agent-linux (AirymaxOS)](https://github.com/openairymax/agent-linux) 管理仓的
> 仓库级 GitHub 配置。本仓的对外说明在根目录
> [README_zh.md](../README_zh.md) / [README.md](../README.md)，本文件只讲
> `.github/` 这一层的布局与约定，不充当仓库首页。

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 目录内容

```text
.github/
├── LAYOUT.md                    # 本文件：本层布局与约定
├── workflows/                   # 8 个工作流；清单与触发矩阵见其 README
│   └── README.md                # 触发/作业矩阵的唯一事实来源
├── ISSUE_TEMPLATE/              # 缺陷 / 需求 / 设计三类 issue 模板
└── PULL_REQUEST_TEMPLATE.md     # DCO、子系统前缀、[SC] 声明
```

文件名刻意不叫 `README.md`：GitHub 在任何目录遇到 `README.md` 都会尝试渲染，
而本仓的首页是根目录那两份。用 `LAYOUT.md` 就没有第二个候选。

## 权威归属

每个工作流的触发条件与作业矩阵只写一遍，写在
[`workflows/README.md`](workflows/README.md)。本文件不重复——早先那张重复的表
在 manifest 定稿 28 个矩阵单元之后仍报 31，正是重复带来的漂移。

| 问题 | 权威出处 |
|------|----------|
| 某个工作流由什么触发，跑哪些作业？ | [`workflows/README.md`](workflows/README.md) |
| 哪些矩阵单元、排除项与超时是合法的？ | `tools/ci-matrix/manifest.yaml`（由 `tools/ci-matrix/ci_matrix.py --check` 校验） |
| 一个工作流最多能留几段内联 `run:`？ | `ci/workflow-budgets.txt`（棘轮，由 `ci/check-workflows.py` 校验） |
| 某条门禁断言什么，本地怎么复跑？ | `ci/gates/*.sh` 与 `ci/ci.sh --list` |
| 流水线为什么是这个形状？ | hub 仓库 `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` |

## 约定

- **文件名**：kebab-case `^[a-z0-9]+(-[a-z0-9]+)*\.yml$`；`name:` 必须等于文件名主干。
- **后缀**：`-verify` 表示阻断性门禁；`image-build` / `nightly` / `release` 以它
  所负责的生命周期阶段命名。
- **载荷**：YAML 只做派遣层——checkout → `ci/ci.sh <gate>` → 上传产物。
- **作业数**：每个工作流 2 个。
- **分支保护**：L1/L3 评审走 GitHub rules（`require_code_owner_reviews`，2 名
  评审人），不砌成 CI 作业。
- **密钥**：`GPG_PRIVATE_KEY`、`GPG_PASSPHRASE`、`COSIGN_KEY`、`COSIGN_PASSWORD`，
  任何情况下都不回显。

新增或改名工作流要同步改三处：文件本身、`name:`、`ci/workflow-budgets.txt`；
`./ci/lint.sh` 会拒绝漂移。

## 许可

双许可 **AGPL v3 + Apache 2.0**（SPDX：`AGPL-3.0-or-later OR Apache-2.0`）。
见仓库根目录 [LICENSE](../LICENSE) 与 [NOTICE](../NOTICE)。
