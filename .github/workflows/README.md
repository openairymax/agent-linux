Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

# `.github/workflows/` — AirymaxOS 持续集成流水线

> agent-linux（AirymaxOS）管理仓的 GitHub Actions 定义。每个文件只做三件事：
> 检出、调用 `ci/ci.sh <gate>`、上传产物；所有判据都在 `ci/` 下，可在本地
> 用同一条命令复跑。

---

## 1. 定位：YAML 是派遣层，不是判据所在

流水线曾经把 78 段 shell 直接写在 `run: |` 里。后果不是「写得不好看」，而是
判据只在云端存在：改一行要在多个 YAML 之间同步，失败只能重跑整条流水线才能
知道哪一句断了，本地无从验证。

现在的分工是硬约束：

- **YAML**：触发条件、权限、并发、超时、`ci/ci.sh <gate>`、产物上传。
- **`ci/gates/*.sh`**：全部断言与宿主准备，带 `# 用法:` 头，可直接执行。
- **`ci/lib/*.sh`**：跨门禁共用的实现（apt 获取、宿主清理、TAP 解析等）。

这条分工由 `ci/check-workflows.py` 的十二条规则强制执行（见第 4 节），而不是
靠评审时的自觉。一个约定如果没有执行者，它就不是约定。

## 2. 目录内容

```text
.github/workflows/
├── README.md              # 本文件
├── airy-verify.yml        # Airymax 内核扩展：四架构构建 + 静态检查
├── image-build.yml        # 桌面 ISO 组装 + 装机冷启动验收
├── kernel-verify.yml      # 内核构建矩阵 + 静态分析 + 测试
├── mgmt-verify.yml        # 治理文件完整性 + 叶仓 CI 汇总
├── nightly.yml            # 夜间测试套件 + 自动回退/预算检查
├── os-chk-verify.yml      # OS-CHK 统一门禁编排（P0/P1 阻断）
├── release.yml            # 构建 + SBOM + 签名 + 发布
└── ssot-verify.yml        # SSoT 语法与交叉引用校验
```

八个流水线，每个最多两个作业（`os-chk-verify` 为一个）。作业数上限登记在
`ci/workflow-budgets.txt`，只许下调。

## 3. 流水线清单

| 文件 | 作业 | 触发 | 守什么 |
|------|------|------|--------|
| `kernel-verify.yml` | `kernel-build`、`kernel-verify` | `kernel` gitlink、`ci/gates/kernel-*.sh`、`ci/lib/**` 的 PR 与 push | 构建矩阵为 4 配置 × 4 架构 × 2 编译器减 4 个排除格，共 28 格，`max-parallel: 8`；`W=2` 零告警。第二作业跑 checkpatch `--strict`、sparse `C=2`、Coccinelle、UML 上的 KUnit 与 QEMU 上的 kselftest |
| `airy-verify.yml` | `airy-build`、`airy-static` | `kernel` gitlink、`ci/gates/kernel-*.sh`、`ci/lib/**` 的 PR 与 push | `airy_defconfig` 在 x86_64/aarch64/riscv64/loongarch64 上构建；断言 Airymax CONFIG、BTF 内嵌、六个 `built-in.a` 产物、`security/airy/` 与 `kernel/superv/` 的符号不冲突。`airy-static` 只审 SPDX 标识 |
| `os-chk-verify.yml` | `os-chk-verify` | `tools/**`、`sdk/**`、`ci/**` 与四个受审 gitlink 的 PR 与 push | 解析 `gates.yaml`，分派已登记的 OS-CHK 执行器，汇总报告并阻断 P0/P1 级结论。同时承载整套 Python 工具链（abi-check、codegen、dsl、farm、flash、rollback 等）：一次依赖安装、一次 pytest，工具改动不再排队四轮 |
| `mgmt-verify.yml` | `file-integrity`、`orchestrate-leaf-ci` | 文档、`.gitmodules`、`ssot-registry.yaml`、`MAINTAINERS`、`CODEOWNERS`、`tools/**`、`.github/**`、`kernel` 的 PR，以及 `main` 的 push | 校验由 `.gitmodules` 推导的子模块目录、12 份 `[SC]` 共享头、`ipc.h` 的物理唯一副本、12 份治理文件、不得出现 `airymaxos-` 前缀；第二作业汇总叶仓 CI、跑 markdownlint 与版权头检查 |
| `image-build.yml` | `assemble`、`accept` | 仅 `workflow_dispatch`（发行门禁，不参与 PR） | `assemble` 先把构建工具链预热成可离线自证的 dnf 缓存树，再在特权官方容器内以钉版的 openEuler netinst 为基底组装桌面 ISO，校验装配清单与 ISO 校验和；`accept` 在 QEMU 下分别以 BIOS 与 UEFI 安装，冷启动到 `AIRYMAX-DESKTOP-READY`（`acceptance=true` 时为 `AIRYMAX-SMOKE-PASS`），发布 TAP 与串口日志 |
| `nightly.yml` | `nightly-test-suite`、`nightly-revert-or-budget` | cron `0 2 * * *` 与手动 | 形式化验证、72 小时浸泡、混沌注入（CPU 热插拔、内存热移除、I/O 错误、网络分区）；第二作业在回归时经 `auto-bisect` 自动回退，或做 CI 预算检查（稳态 60 分钟，内核冷缓存 90 分钟） |
| `release.yml` | `build-and-sign`、`publish-release` | 标签 `v*`、`v*-rc*` 与手动 | 逐子模块 SBOM 合并、内核 RPM、GPG 与 cosign 签名、SDK 压缩包；第二作业校验已签名产物、推送 OCI 镜像到 ghcr、清理过期资产、创建 GitHub Release |
| `ssot-verify.yml` | `ssot-syntax-and-rules`、`doc-links` | 文档、`ssot-registry.yaml`、`tools/validate-ssot.py` 的 PR 与 `main` 的 push | PyYAML 语法、规则 ID 计数与唯一性、`validate-ssot.py` 交叉引用；相对路径链接完整性 |

`nightly.yml` 与 `release.yml`、`image-build.yml` 暴露 `workflow_dispatch`。
`image-build.yml` 的参数含义：

- `acceptance: true` — 生成验收 ISO，并断言会话内出现 `AIRYMAX-SMOKE-PASS`。
- `release_tag` — 把产品 ISO 发布到 `ghcr.io`；发布按 `(tag, digest)` 幂等，
  已有同名标签时拒绝替换，除非显式 `replace: true`。重打同一版本是一个决定，
  不是一个副作用。

## 4. 命名与结构约定

规则实现在 `ci/check-workflows.py`，由 `ci/lint.sh` 在 `os-chk-verify` 与本地
lint 中执行。下表是规则本身，不是建议。

| 方面 | 规则 | 为什么 |
|------|------|--------|
| 文件名 | kebab-case，`^[a-z0-9]+(-[a-z0-9]+)*\.yml$` | 任何文件系统与区域设置下大小写、排序都一致 |
| `name:` | 与文件名主干逐字相同 | UI 显示名与文件路径只能有一处事实源，否则 Actions 列表、`gh workflow run` 与文档会互相矛盾 |
| `-verify` 后缀 | 只留给阻断型门禁 | 读名字就知道违规会红 |
| 生命周期名 | `image-build` / `nightly` / `release` | 按所属生命周期阶段命名，不按调用的工具命名 |
| 载荷 | YAML 只做检出、`ci/ci.sh`、上传 | 断言全部下沉，可对遗留构建目录复跑 |
| 作业数 | 每文件上限见 `ci/workflow-budgets.txt`（最多 2） | 保持 DAG 可读、关键路径短 |
| 内联 `run` | 棘轮预算，只许下调；预算为 0 时额外禁用一批模式 | 防止 shell 逻辑一段段爬回 YAML |
| `permissions:` | 顶层显式声明，禁止 `write-all` 与 `*: write` | 未声明时令牌权限随仓库设置漂移，同一份 YAML 在不同仓库权限不同 |
| `concurrency:` | PR 触发的流水线必须声明，且取消进行中的旧轮 | 被取代的 run 的结论不描述当前树，留着只会占并发额度、并在排障时被误读 |
| `timeout-minutes:` | 每个作业必填 | 缺省 360 分钟等于没有上限；获取卡死会一路烧到配额尽头 |
| 运行器镜像 | 钉版（`ubuntu-24.04`），不用 `ubuntu-latest` | 浮动标签会悄悄换掉编译器、shellcheck 与 Python 版本，门禁结论就取决于跑的那一天 |
| 触发路径对等 | `pull_request` 的 paths 必须是 `push` 的子集 | PR 独有的路径意味着该文件合并后不再被校验 |
| 子模块路径 | 只写 gitlink 目录（`kernel`），不写 `kernel/**` | 父仓 diff 里子模块只有目录一项，内部 glob 永不命中，是死判据 |
| 门禁存在性 | 每个 `ci/ci.sh <gate>` 必须有同名 `ci/gates/<gate>.sh` 且自带 `# 用法:` | 名字写错要到 checkout 与预热之后才以 exit 2 暴露；本地一秒可查 |
| 库函数来源 | 门禁只能调用它 `source` 过的库函数 | 未 source 的 `need_cmd` 只会变成 “command not found”，前置校验静默失效 |

新增或改名一个流水线需要三处同步：文件、`name:`、`ci/workflow-budgets.txt`。
`./ci/lint.sh` 在任一处漂移时直接判红。

## 5. 构建速度的纪律

提速只允许在「不减少覆盖面」的方向上做。已落实的三条：

1. **工具链预热自证**：`image-build` 的 `assemble` 先用一次 dnf 预热把 1200
   余个包落进缓存树，并在离线状态自证可解析，之后每次组装只做增量。缓存键
   绑定预热自证的结果，而不是绑定 YAML 的文本——绑定文本时，改了无关的一行
   就会让整棵缓存失效，重新在线取包。
2. **缓存命中判据前置**：预热门禁在进入 ISO 组装前判定缓存是否可用，未命中
   时才在线补齐，避免「先跑十分钟再失败」。
3. **并发取消与显式超时**：同一 PR 的旧轮被取消，每个作业都有上限，挂住的
   作业不会占着运行器直到队列放弃。

工具链门禁本身也有时限：每条外部命令进入固定的时限数组，单条命令不可无限
等待。这条约束来自一次真实的自锁——缓存键依赖门的结论、门的结论又依赖缓存
命中，两边互等，流水线只能在超时后失败。

## 6. 本地复跑

每个门禁都是普通脚本，因此同一套断言可以在本地对遗留的构建目录执行：

```bash
./ci/ci.sh --list                       # 列出全部门禁及其用法
./ci/ci.sh image-verify --out <dir>     # 复核已有 image-build 产物
ACCEPTANCE=true ./ci/ci.sh tap-extract --dir <dir>
./ci/lint.sh                            # 工作流规则 + 门禁 + SSoT 本地校验
python3 tools/validate-ssot.py . ssot-registry.yaml
```

## 7. 排查

- 查某次运行的作业与步骤：
  `gh run view <run-id> --log`，或按 `run_id` 调 GitHub API 的
  `actions/runs/<id>/jobs`。
- `image-build` 的产物在 `out/desktop/iso/*.iso` 与同名 `.sha256`，以产物形式
  上传；`accept` 的 TAP 与串口日志同样以产物形式保留，用于归因「装上了但没
  进桌面」与「没装上」这两类不同故障。
- 父仓的 `kernel`、`system`、`distro` 等以 gitlink 固定 SHA。CI 检出的是该
  SHA 的内容，因此未推到远端的本地提交会让 checkout 失败（远端报
  `upload-pack: not our ref`），这不是权限问题，是对象不在远端。

## 8. 许可

双许可 **AGPL v3 + Apache 2.0**（SPDX：`AGPL-3.0-or-later OR Apache-2.0`）。
详见仓库根目录的 [LICENSE](../../LICENSE) 与 [NOTICE](../../NOTICE)。

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
