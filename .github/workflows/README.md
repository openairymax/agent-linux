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

这条分工由 `ci/check-workflows.py` 的十六条规则强制执行（见第 4 节），而不是
靠评审时的自觉。一个约定如果没有执行者，它就不是约定。第 4 节的表格首列
登记规则号，其覆盖范围由脚本反过来校验：文档漏登或重号同样判红。

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
├── payload-seed.yml       # 定时把闭包池推成按输入指纹命名的 seed 层
├── release.yml            # 构建 + SBOM + 签名 + 发布
└── ssot-verify.yml        # SSoT 语法与交叉引用校验
```

九个流水线，每个最多两个作业（`os-chk-verify` 为一个）。作业数上限登记在
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
| `payload-seed.yml` | `probe`、`warm` | cron `17 19 * * *`（北京时间 03:17）与手动 | `probe` 对实时上游取五段仓库元数据，算出 `seed-<arch>-<key16>`；`warm` 恢复上一轮构建留下的闭包池，仅当池的输入标签与该指纹一致时把它推成 seed 层，上游不可达或指纹已变则退 0 并说明原因。预热只搬运已有字节，不下载任何 RPM，因此也不得成为构建的先决条件 |
| `release.yml` | `build-and-sign`、`publish-release` | 标签 `v*`、`v*-rc*` 与手动 | 逐子模块 SBOM 合并、内核 RPM、GPG 与 cosign 签名、SDK 压缩包；第二作业校验已签名产物、推送 OCI 镜像到 ghcr、清理过期资产、创建 GitHub Release |
| `ssot-verify.yml` | `ssot-syntax-and-rules`、`doc-links` | 文档、`ssot-registry.yaml`、`tools/repo/validate-ssot.py` 的 PR 与 `main` 的 push | PyYAML 语法、规则 ID 计数与唯一性、`validate-ssot.py` 交叉引用；相对路径链接完整性 |

`nightly.yml` 与 `release.yml`、`image-build.yml`、`payload-seed.yml` 暴露
`workflow_dispatch`。
`image-build.yml` 的参数含义：

- `acceptance: true` — 生成验收 ISO，并断言会话内出现 `AIRYMAX-SMOKE-PASS`。
- `release_tag` — 把产品 ISO 发布到 `ghcr.io`；发布按 `(tag, digest)` 幂等，
  已有同名标签时拒绝替换，除非显式 `replace: true`。重打同一版本是一个决定，
  不是一个副作用。

## 4. 命名与结构约定

规则实现在 `ci/check-workflows.py`，由 `ci/lint.sh` 在 `os-chk-verify` 与本地
lint 中执行。下表是规则本身，不是建议。

| # | 规则 | 为什么 |
|---|------|--------|
| 1 | 结构上限：每个 workflow 的 job 数与内联 `run: \|` 块数不得超过 `ci/workflow-budgets.txt` 的登记值，预算只许下调 | 「薄化」要有可测量的上限，否则一次搬迁之后又会重新长回来 |
| 2 | 棘轮：内联块数不得高于 `git HEAD` 中的同名文件 | 调低预算才算迁移；把逻辑抄回 YAML 会立即变红 |
| 3 | 内联预算为 0 的文件不得出现已被门禁收编的模式：宿主清理、`docker load`、摘要校验、QEMU 安装、串口 TAP 解析 | 同一段断言有两份实现，改一处必漏一处 |
| 4 | 不得在 YAML 里直接调用 `apt-get`，统一走 `ci/lib/apt.sh` | 获取超时、重试与硬超时只有一处实现，否则一次镜像不响应就烧光整步预算 |
| 5 | 命名约定：文件名为 kebab-case 的 `<scope>[-<scope>…].yml`，且顶层 `name:` 与文件名主干逐字相同 | UI 显示名与文件路径只能有一处事实源，否则 Actions 列表、`gh workflow run` 与文档互相矛盾 |
| 6 | 门禁脚本只能调用它 `source` 过的库函数 | 未 `source` 的 `need_cmd` 只会变成 "command not found"，前置校验静默失效 |
| 7 | 顶层必须显式声明 `permissions:`，禁止 `write-all` 与 `*: write` | 未声明时令牌权限随仓库设置漂移，同一份 YAML 在不同仓库权限不同 |
| 8 | 每个 job 必须有 `timeout-minutes:` | 缺省 360 分钟等于没有上限；获取卡死会一路烧到配额尽头 |
| 9 | PR 触发的流水线必须声明 `concurrency:`，并取消进行中的旧轮 | 被取代的 run 的结论不描述当前树，留着只会占并发额度、并在排障时被误读 |
| 10 | `pull_request` 的 paths 必须是 `push` 的 paths 子集 | PR 独有的路径意味着该文件合并后不再被校验 |
| 11 | paths 只写 gitlink 目录（`kernel`），不写 `kernel/**` | 父仓 diff 里子模块只有目录一项，内部 glob 永不命中，是死判据 |
| 12 | 每个 `ci/ci.sh <gate>` 必须有同名 `ci/gates/<gate>.sh`，且该脚本自带 `# 用法:` 头 | 名字写错要到 checkout 与工具链预热之后才以 exit 2 暴露 |
| 13 | 每个 `runs-on:` 必须钉成 `<发行版>-<YY.MM>`（如 `ubuntu-24.04`），不得用 `-latest` | 浮动标签会悄悄换掉编译器、shellcheck 与 Python 版本，门禁结论就取决于跑的那一天 |
| 14 | 文件名分两族：`*-verify` 必须由 `pull_request` 触发（阻断型门禁）；其余只能是 `image-build` / `nightly` / `release` / `payload-seed` | 读名字就知道违规会红，也读得出它何时跑 |
| 15 | 本节表格的规则号必须与脚本的规则号一一对应：漏登、多登、重号均判红 | 文档一旦能单独演化，读者就会按错版本执行，新增规则则无人知晓 |
| 16 | 每个 `uses:` 一律钉 40 位提交 SHA，并带 `# vN` 溯源注释（`./…` 与 `docker://` 豁免） | 浮动 tag 可被上游重新指向：代码在两次运行之间被换掉而日志无迹 |

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
python3 tools/repo/validate-ssot.py . ssot-registry.yaml
```

`ci/lint.sh` 在找不到 shellcheck 时只告警跳过，而 runner 上有——于是
「本地全绿、CI 全红」会以纯静态问题的形式反复出现（SC2034 连续两次）。
本地补齐静态工具，让两侧判据一致：

```bash
pipx install shellcheck-py   # 或 pip install --user shellcheck-py
export PATH="$HOME/.local/bin:$PATH"
PATH="$HOME/.local/bin:$PATH" ./ci/lint.sh
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
