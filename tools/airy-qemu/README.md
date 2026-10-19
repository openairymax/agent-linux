<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# airy-qemu — AirymaxOS QEMU 测试农场

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

在 QEMU 中运行内核 kselftest，并以 TAP 证据作为合并门禁的唯一权威。
本工具把一次农场运行拆成职责单一的十二个分片：

| 分片 | 模块 | 职责 |
|------|------|------|
| 01 | `farm_types.py` | 共享类型、异常与资源预算常量 |
| 02 | `farm_manifest.py` | 加载并校验 `manifest.yaml` |
| 03 | `farm_target.py` | 架构 → QEMU 机器/加速器解析 |
| 04 | `farm_cmdline.py` | virtme-ng argv 构造与 rootfs 约束 |
| 05 | `farm_preflight.py` | 内核镜像 magic、kselftest 安装与超时预检 |
| 06 | `farm_runner.py` | 子进程执行、TAP/控制台日志取证、超时回收 |
| 07 | `farm_tapagg.py` | 多套件 TAP 聚合与 SKIP 单独计数 |
| 08 | `farm_verdict.py` | 单作业判定与整批门禁 |
| 09 | `farm_sched.py` | 并发数/内存/CPU 三重预算调度 |
| 10 | `farm_report.py` | 确定性 JSON 报告与控制台摘要 |
| 11 | `farm_cli.py` | 命令行入口与流水线编排 |

执行器只执行与取证，判定只读取证据，报告只渲染结论——任何一层都不
复制其他层的语义。

权威依据：

| 内容 | 出处 |
|------|------|
| OS-TEST-010/013~022（QEMU 运行 kselftest 与 TAP 门禁） | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` §5.3 |
| OS-TEST-014/015/021（KVM 回退、TAP 计数、SKIP 单独统计） | `docs/docs-linux/80-testing/02-kselftest.md` |
| OS-STD-009（测试超时预算） | `docs/docs-linux/80-testing/02-kselftest.md` |
| TAP 计数与退出码契约 | `tools/parse-tap.py` |
| virtme-ng 实际调用基线 | `.github/workflows/ci-kernel.yml` |

## 目录结构

```text
tools/airy-qemu/
├── farm_types.py         # 共享类型地板（无外部依赖）
├── farm_manifest.py      # 清单加载
├── farm_target.py        # 目标解析
├── farm_cmdline.py       # argv 构造
├── farm_preflight.py     # 执行前预检
├── farm_runner.py        # 执行与取证
├── farm_tapagg.py        # TAP 聚合
├── farm_verdict.py       # 判定与门禁
├── farm_sched.py         # 资源预算调度
├── farm_report.py        # 报告渲染
├── farm_cli.py           # 命令行入口
├── manifest.yaml         # 机器、套件与预算的唯一事实源
├── requirements-dev.txt  # pytest 与 PyYAML
└── tests/                # pytest 单元与端到端测试
```

## 清单契约

`manifest.yaml` 是机器定义、套件目录与并行预算的唯一事实源：

```yaml
version: "1.0.0"
machines:
  - arch: x86_64
    machine: q35
    cpu: max
    console: ttyS0
suites:
  - name: seccomp
    timeout_s: 120
limits:
  max_jobs: 2
  mem_mb: 8192
  cpus: 4
```

- 四个架构（`x86_64` / `arm64` / `riscv` / `loongarch`）各一条机器定义；
- 套件名必须是安全标识符，超时为正整数且不得小于套件 `settings` 的实际值；
- `limits` 是调度上界：并发作业数、累计内存与累计 CPU 同时受限。

## 使用方法

在 `agent-linux` 仓库根运行：

```bash
# 本机架构单跑；默认使用清单全集套件
python3 tools/airy-qemu/farm_cli.py \
    --kernel x86_64=arch/x86/boot/bzImage \
    --ksft-dir kselftest-install \
    --out out/farm

# 跨架构（arm64）必须提供 rootfs 磁盘镜像
python3 tools/airy-qemu/farm_cli.py \
    --kernel x86_64=arch/x86/boot/bzImage \
    --kernel arm64=arch/arm64/boot/Image \
    --rootfs rootfs.img \
    --ksft-dir kselftest-install \
    --out out/farm

# 只跑指定套件（按清单顺序生效）；无 KVM 的宿主可强制 TCG
python3 tools/airy-qemu/farm_cli.py \
    --kernel x86_64=arch/x86/boot/bzImage \
    --ksft-dir kselftest-install \
    --out out/farm \
    --suites seccomp,pidfd \
    --accel tcg
```

参数：

| 参数 | 必填 | 说明 |
|------|------|------|
| `--manifest PATH` | 否 | 清单路径，默认本目录 `manifest.yaml` |
| `--kernel ARCH=PATH` | 是 | 每个待测架构一条，可重复；作业 ID 取架构名 |
| `--ksft-dir PATH` | 是 | `make install` 生成的 kselftest 目录 |
| `--out DIR` | 是 | TAP 与控制台日志落点，不存在时创建 |
| `--accel {auto,kvm,tcg}` | 否 | 加速器选择，默认 `auto` |
| `--suites LIST` | 否 | 逗号分隔的套件子集，默认清单全集 |
| `--rootfs PATH` | 跨架构必填 | 异架构 guest 的根文件系统镜像 |
| `--report PATH` | 否 | 报告路径，默认 `OUT/farm.json` |

未知或重复的套件名、缺失内核镜像、跨架构缺 rootfs 均在派发前拒绝，
不会出现部分作业已执行后才失败的情况。

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 门禁通过（允许 `skipped`，不允许 `failed`/`timeout`/`error`） |
| `1` | 门禁失败，阻断作业列在报告 `blocked` 字段 |
| `2` | 用法或配置错误（清单、内核、套件、路径、报告落点非法） |

stdout 只输出控制台摘要，stderr 只输出 `ERROR: ...`；生产代码不使用
`print()`。

## 报告契约

`farm.json` 为确定性文档：无时间戳、无宿主路径探测，同一份证据重复
运行逐字节相同（`sort_keys=True`）。

```json
{
  "version": "1.0.0",
  "gate": "failed",
  "blocked": ["arm64"],
  "counts": {"total": 2, "ok": 1, "blocking": 1},
  "states": {"failed": 1, "passed": 1},
  "jobs": [
    {
      "job_id": "x86_64",
      "state": "passed",
      "duration_s": 12.34,
      "exit_code": 0,
      "reason": "",
      "suites": ["seccomp"],
      "tap": {
        "plan_total": 1,
        "ok_count": 1,
        "not_ok_count": 0,
        "skip_count": 0,
        "executed": 1,
        "missing": 0,
        "complete": true,
        "passed": true
      },
      "qemu_argv": ["vng", "--run", "..."]
    }
  ]
}
```

作业状态语义：

| 状态 | 判定条件 | 阻断门禁 |
|------|----------|----------|
| `passed` | TAP 完整、全绿且退出码为 0 | 否 |
| `skipped` | TAP 完整、无 not ok，全部用例带 `# SKIP` | 否 |
| `failed` | TAP 不完整、存在 not ok，或 TAP 全绿但退出码非零 | 是 |
| `timeout` | 超过作业墙钟预算，进程组已回收 | 是 |
| `error` | TAP 缺失、不可读或不合法 | 是 |

`missing` 为计划中缺失结果的用例数；`complete` 要求 `plan_total > 0` 且
`missing == 0`。TAP 是权威证据，退出码是补充证据。

## CI 接入

`.github/workflows/farm-verify.yml` 在 `tools/airy-qemu/**` 变更时运行
本工具的 pytest 套件（含端到端流水线与 CI/文档契约测试）。测试以假执行器
替代真实 QEMU，不依赖 KVM 或 virtme-ng；真实 QEMU 上的 kselftest 仍由
`ci-kernel` 的 virtme-ng 链路承担。

## 单元测试

```bash
pip install -r tools/airy-qemu/requirements-dev.txt
python3 -m pytest tools/airy-qemu/tests/ -q
```

测试覆盖清单解析、目标解析、argv 构造、预检、执行取证、TAP 聚合、
判定、调度、报告、CLI 与端到端双架构门禁；所有子系统测试均可在无
虚拟化能力的开发机上运行。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
