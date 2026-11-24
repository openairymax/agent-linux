Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

# `ci/` — 仓库级门禁层

> 本目录是 AirymaxOS 全部可执行判据的所在。流水线只负责检出、调用
> `ci/ci.sh <gate>` 与上传产物；判据本体、共享实现、回归夹具都在这里，
> 因此同一条命令在开发机与 runner 上行为一致。

---

## 1. 分层与归属

一条判据落进哪个目录，不由文件大小决定，由「谁消费它」决定：

| 位置 | 放什么 | 不放什么 |
|------|--------|----------|
| `ci.sh` / `lint.sh` | 唯一的两个入口：门禁分发与 CI 层自检 | 任何第三条根级脚本 |
| `gates/` | 一个文件一条判据，自带用法与退出码声明 | 被多个门禁共享的实现 |
| `lib/` | 跨门禁共享实现：宿主准备、apt、日志、计时、Python 解析器 | 只被一个门禁使用的逻辑 |
| `checks/` | Python 检查器本体（度量、桩扫描、死代码、文档链接、工作流规则等） | 会被流水线直接调用的脚本 |
| `baseline/` | 棘轮基线：度量、workflow 预算、构建分段 | 任何可执行脚本 |
| `tests/` | 门禁自身的回归夹具（正向 + 负向） | 产品测试 |
| `tools/` | 门禁私有的取证工具（回归器、字段采集器） | 对外命令面 |
| `container/` | 基础镜像的 Containerfile 与包清单 | 镜像构建脚本 |

两处最容易放错：

- `ci/tools/` 不是仓根 `tools/`。前者是被某条门禁调用的取证工具，后者是
  跨子模块的命令面（唯一入口 `tools/airy`）。两者互不引用。
- 三态（0／1／3）与 `CI_EINVAL`（5）的取值是 §4 的全局约定；单个门禁用到的
  常量与辅助函数留在该门禁文件里，不另建子目录存放只被一处使用的声明。

`ci/` 根只有 `ci.sh`、`lint.sh` 与本文件三项。散落的临时脚本一律判红：
判据没有归属，下一轮就会有人照着旧副本改。

## 2. 目录内容

```text
ci/
├── README.md            # 本文件：归属与规则
├── ci.sh                # 门禁分发器（--list 读取各门的「用法:」头）
├── lint.sh              # CI 层自检（20 项，见文件头）
├── gates/               # 一个文件一条判据
│   └── lib/             # 门禁协议声明（Q-04 施工图纸钉定此路径）
├── checks/              # Python 检查器
├── lib/                 # 共享实现（shell + python）
├── baseline/            # 棘轮基线与门禁归属登记
├── tests/               # 门禁回归夹具
├── tools/               # 门禁私有取证工具（回归器、字段采集器）
└── container/           # 基础镜像定义与包清单
```

门禁与流水线的对应关系以
[`.github/workflows/README.md`](../.github/workflows/README.md) 第 3 节为准；
本文件只定归属与命名，不复制那份清单——两份清单必然在某个提交后分叉。

## 3. 门禁分类

按「谁在什么时机消费」分三类，分类决定脚本写在哪里、失败是否阻断：

| 类别 | 例子 | 消费方 | 失败语义 |
|------|------|--------|----------|
| 阻断型 | `kernel-*`、`tui-*`、`mgmt-*`、`lint`、`no-stub`、`wp-scope` | `*-verify` 流水线的 PR 触发 | 立刻判红，不进后续步骤 |
| 交付型 | `release-*`、`assemble`、`accept`、`iso-*`、`channel-*`、`attest` | `release`、`image-build` | 判红并保留分段日志，产物不上传 |
| 运维型 | `qemu-setup`、`rtc-probe`、`backlight`、`desktop-status`、`model-manifest` | 定时与派发流水线、操作员手工 | 判红或按显式「不适用」说明退出 |

族的语义由前缀承担：`kernel-` 是内核树判据，`release-` 是发布物料判据，
`tui-` 是 TUI 工作区与运行面判据，`build-`／`image-`／`iso-`／`payload-`
是镜像链路判据，`mgmt-` 是治理文件判据。前缀相同的脚本必须能一起读，
否则前缀就只是命名噪声。

## 4. 命名与编写约定

| 对象 | 约定 | 例 |
|------|------|----|
| 门禁 | kebab-case，读作「对象-动作」；一词一义 | `kernel-kunit.sh`、`wp-scope.sh` |
| 检查器 | snake_case `.py`，模块名即判据名 | `quality_metrics.py` |
| 共享实现 | 按域命名，不做动作前缀 | `host.sh`、`rs_src.py` |
| 回归夹具 | `test_<门禁>.sh`，与门禁同名同序 | `test_wp_scope.sh` |
| 基线 | 固定三名，由 `--update` 写入 | `quality-baseline.json` |

每条门禁的头部必须依次给出：用途、`# 用法:`（`ci/ci.sh --list` 直接读这一行）、
`# 退出码:`、依赖的前置产物。其中「退出码」段的存在、命令位字面 `exit N` 的取值、
`readonly EINVAL=` 的取值与 `bash -n` 语法由 `ci/gates/tri-state.sh` 审计；
缺少「用法:」不会判负，但 `--list` 会显示「（未登记用法）」——读者拿不到入口，
等价于这条判据不存在。

退出码只允许四种：`0` 通过、`1` 判负、`3` 不适用、`5` 参数或前置不满足。
「不适用」必须打印原因；「判不了」是 5，不是 3——把两者混同，跳过就不再可信。
参数错误一律用 `EINVAL`／`CI_EINVAL`（值恒为 5），不得自造取值。

门禁只允许调用它 `source` 过的库函数；这与 `ci/checks/check-workflows.py`
第 6 条同源。共享实现被两个以上门禁使用时才下沉到 `lib/`，只有一个消费者
时留在原地——提前抽层会让两处逻辑互相牵制。

## 5. 新增一条门禁的落位顺序

1. 先定消费方：属于哪一类、由哪条流水线在什么时机调用。没有消费方的判据
   不新增，先回到需求确认。
2. 写 `gates/<name>.sh`：四段头 + 单一判据 + 明确的退出码。逻辑超过一个
   判据就拆文件，不用开关参数合并。
3. 共享部分进 `lib/`；只有一个消费者的实现不抽层，留在该门禁文件里。三态与
   `CI_EINVAL` 的取值是 §4 的全局约定，新门禁照着写，不另立一套常量。
4. 在 `ci/tests/` 补回归夹具：至少一条负向（判据真的能判负）与一条正向。
   `./ci/lint.sh` 的第 7 项会跑它们，真实树常年为绿不会掩盖判据失效。
5. 在流水线里只加一步 `ci/ci.sh <name>`；新增作业时同步
   `baseline/workflow-budgets.txt` 与 `.github/workflows/README.md`，
   任一处漂移 `check-workflows.py` 立即判红。只能在特定现场手工执行的判据
   （需要真机、两轮构建产物、宿主镜像出口）没有调用方，必须在
   `baseline/gate-owners.tsv` 登记 `manual` 与现场依赖；不登记即按无调用方
   判红，登记项指向不存在的门禁同样判红。
6. 产出棘轮基线时，只允许 `--update` 显式改写，且基线数量只减不增；手改
   数字与删除判据等价。

## 6. 本地复跑

```bash
./ci/lint.sh                                   # CI 层全量自检
./ci/ci.sh --list                              # 可用门禁及其用法
python3 ci/checks/quality_metrics.py --check   # 度量棘轮
python3 ci/checks/check-workflows.py           # 流水线薄化与命名规则
```

各门禁的具体参数由脚本头部的「用法:」行给出；`ci/ci.sh <gate> -h` 与直调
等价。宿主缺少依赖时门禁退 5 并说明缺什么，不用「跳过」冒充通过。

## 7. 许可

双许可 **AGPL v3 + Apache 2.0**（SPDX：`AGPL-3.0-or-later OR Apache-2.0`）。
详见仓库根目录的 [LICENSE](../LICENSE) 与 [NOTICE](../NOTICE)。

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
