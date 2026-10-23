<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# osaccrt — OS-ACC-RT 运行态验收执行器

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

本目录承接模块 V 的 V-01（OS-ACC-RT 标准与执行器），把验收规程的
**准备 → 执行 → 取证 → 判定 → 复核 → 归档** 六步固化为唯一代码入口：
实验数据只能经 `Store` 落盘，判定只能读已登记证据，任何步骤都无法旁路。

权威依据：

| 内容 | 出处 |
|------|------|
| 六步规程、判定状态机、证据包规范、四态语义 | `docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md` §3 |
| 实验矩阵 E1~E7 与阈值来源 | 同上 §2 |
| 模块 V 任务分片（TV-01-01~TV-01-11） | `docs/docs-closed/agent-linux-dev/0.0.3-极境OS任务方案.md` §6.9.1 |
| NFR 原值（NFR-P-001~P-006、NFR-S-001、NFR-R-001） | `docs/docs-linux/00-requirements/03-non-functional-requirements.md` |

---

## 目录内容

```text
tools/osaccrt/
├── osaccrt_model.py      # 数据模型：Verdict/Step/Threshold/Evidence/ExpResult/Judge 协议
├── osaccrt_codec.py      # JSON 读入、字段集合与文本类型校验
├── osaccrt_spec.py       # TOML 阈值表加载与 schema 校验
├── osaccrt_evidence.py   # Manifest 清单、证据槽位、sha256 链
├── osaccrt_store.py      # append-only 证据库：新建/写入/封口/复核/链头
├── osaccrt_judge.py      # PackJudge 判定状态机
├── osaccrt_waiver.py     # FAIL → WAIVED 双签工作流
├── osaccrt_blocked.py    # BLOCKED 预登记与降级台账
├── osaccrt_repro.py      # 复现脚本生成器（离线复核摘要与链头）
├── osaccrt_runner.py     # 执行器主循环、断点续跑与复核回调
├── osaccrt_boot.py       # 串口启动日志解析：标记、TAP 与关键链原文转发
├── osaccrt_img.py        # 装机清单解析：镜像摘要与逐固件结果
├── osaccrt_smoke.py      # 冷启动冒烟判定（NFR-P-005 boot.cold）
├── osaccrt_bootchain.py  # 冷启动关键链判定（NFR-P-005 boot.service）
├── specs/nfr.toml        # 阈值表（唯一事实源，源码随发布）
├── requirements-dev.txt  # 测试依赖（pytest）
└── tests/                # pytest 套件（16 个模块，含 test_v01_judge.py 全链基线）
```

## 六步规程与代码入口

| 步骤 | 代码入口 | 固化约束 |
|------|----------|----------|
| 准备 | `spec.load_spec`、`Runner.run` | 阈值逐条引 `source`；`blocked.apply` 是 BLOCKED 唯一入口 |
| 执行 | `Runner.run` / `Runner.run_all` | 只接受 PREPARE→RUN→CAPTURE 有序前缀；已取证步骤跳过，部分采集拒绝续跑 |
| 取证 | `Store.write`、`Manifest` | append-only 原子新建；路径限定六步槽位；逐条登记 sha256 |
| 判定 | `PackJudge.decide` | 只读证据包；阈值缺产物即 FAIL；声明与证据不符降为 FAIL |
| 复核 | `Runner.run(..., review)`、`waiver.apply` | 复核只写 `step-05-review.json`，不得丢弃既有证据 |
| 归档 | `Store.seal`、`repro.emit`、`blocked.ledger` | 前链头必须衔接；封口后只读；复现脚本离线复核摘要与链头 |

## 数据模型

- `Verdict`：`PASS` / `FAIL` / `WAIVED` / `BLOCKED`，与 §3.2 状态机一一对应；
- `Step`：`PREPARE` / `RUN` / `CAPTURE` / `VERDICT` / `REVIEW` / `ARCHIVE`；
- `Threshold`：`key`、`source`、`limit`、`unit`，`limit` 必须是比较表达式
  （如 `< 30`），`source` 必须是 `NFR-*-NNN` 或 `OS-ACC-*-NNN`；
- `Evidence`：包内相对路径 + 64 位小写 sha256 + 所属步骤；
- `ExpResult`：实验编号、阈值表、证据列表、判定与双签；构造即校验——
  `WAIVED` 必须携带两个互异邮箱，`BLOCKED` 必须给出降级原因，其余状态
  不得携带二者。

## 阈值表

`specs/nfr.toml` 由评审时从 NFR 原文抄录，运行时不做 Markdown 解析：

```toml
schema = 1

[[threshold]]
key = "boot.cold"
source = "NFR-P-005"
limit = "< 30"
unit = "s"
```

加载即校验根字段集合、`schema` 版本、键命名、来源编号、比较表达式与键
唯一性；任一不满足即整表拒绝，避免带病阈值进入执行。表按实验矩阵闭合，
未被 E1~E7 引用的 NFR 不收录。

## 证据包

```text
evidence/
  <YYYY-MM-DD>/
    <exp-id>/
      manifest.json          # exp_id/date/host/image_sha256/thresholds/evidences/
                             # verdict/prev_chain_head/waiver_sig?/blocked_reason?/supersedes?
      step-01-prepare.json
      step-02-run.log
      step-03-artifacts/<阈值键>   # 键即产物名，判定按此对账
      step-04-verdict.json
      step-05-review.json
      chain.sha256
```

`Manifest.chain_head` 把前链头、实验编号与每条证据的步骤/路径/摘要依次
喂入 sha256，再绑定清单元数据。任一字段被替换都会改变链头，跨包拼接与
静默改写因此不可行。目录 append-only：包已存在即拒绝新建，封口后拒绝
写入，需回写只能新开日期目录并以 `supersedes` 声明替代关系。

## 判定规则

`PackJudge.decide(result)`：

| 输入 | 输出 |
|------|------|
| 准备/执行/取证三步齐全，且每条阈值在 `step-03-artifacts/` 有同名产物 | `PASS` |
| 任一步缺失、阈值无产物、声明 `PASS` 但证据不足 | `FAIL` |
| 声明 `WAIVED` 且双签与证据包一致 | `WAIVED` |
| 声明 `BLOCKED`，仅准备证据且原因在预登记清单内 | `BLOCKED` |
| 出现执行/取证证据的 `BLOCKED`，或 `WAIVED` 缺少双签 | `FAIL` |

数值比较属于各实验域职责（启动冒烟、长稳、性能等），判定器只做状态机
与可追溯性裁决。

## WAIVED 与 BLOCKED

- `waiver.Waiver(basis, reason, evidence, signers)`：`basis` 取
  `equivalent`（有等价替代证据，必须给出 `evidence`）或 `accepted`
  （影响面经评审可接受）；`waiver.apply` 只接受 `FAIL`，返回新对象，
  原结果不被修改。
- `blocked.REGISTRY` 预登记三项实机缺失：`sw_64`、`loongarch`、`m2-smp`，
  每项写明受影响实验与允许的降级方式；未登记键、非本实验、出现执行或
  取证证据一律拒绝。BLOCKED 不计入通过率，但必须出现在
  `blocked.ledger` 台账中。

## 复现脚本

```python
from pathlib import Path
import osaccrt_evidence as oe
import osaccrt_repro as orep

man = oe.load(Path("evidence/2026-10-20/E1-02/manifest.json").read_text())
orep.emit(man, "bash distro/ci/run-image-ci.sh", "repro-e1-02.sh")
```

生成的脚本自带实验元数据、逐条 sha256 与链头；默认只做离线复核，
`--run` 才在临时目录重放原始命令。未判定、证据被篡改或链头不符时脚本
以非零码退出。

## 执行器

```python
import osaccrt_model as om
import osaccrt_runner as orun
import osaccrt_store as ost
import osaccrt_judge as oj

store = ost.Store("evidence")
runner = orun.Runner(store, oj.PackJudge(), "qemu-x86_64", image_sha256)
plan = orun.ExpPlan("E1-02", (threshold,), (
    (om.Step.PREPARE, prepare),
    (om.Step.RUN, run),
    (om.Step.CAPTURE, capture),
))
outcome = runner.run(plan)
```

- 步骤函数签名为 `StepFn = Callable[[Path], Mapping[str, bytes]]`，返回
  「包内路径 → 内容」，由执行器原子落盘并登记摘要；
- 已封口包直接复用结论（`Outcome.resumed` 标识）；未封口包按磁盘证据
  跳过对应步骤；
- 复核通过 `review` 回调注入，返回 `Ruling(result, records)`，记录只能写
  `step-05-review.json`；
- 只含准备步的计划必须提供 `review`，用于实机缺失的诚实降级。

## 测试

```bash
python3 -m pytest tools/osaccrt/tests/ -q
```

`tests/test_v01_judge.py` 固定四条路径的验收基线：PASS 首包前链头为空、
FAIL→PASS 单链续接、FAIL→WAIVED 双签与复核记录随包封口、准备步→BLOCKED
不入通过率。`os-chk-verify.yml` 在监听 `tools/osaccrt/**` 的同时执行本
目录 pytest，作为 L0 门禁。

## 约束

- 证据目录只经 `Store` 读写，任何实验不得旁路或回写历史日期目录；
- 判定器不读取文件系统，只消费 `ExpResult`，避免“先看结论再找证据”；
- 所有路径为包内相对 POSIX 路径，拒绝绝对路径与 `..` 逃逸；
- 除 `pathlib`/`hashlib` 等标准库外无第三方运行时依赖；pytest 仅用于测试。
