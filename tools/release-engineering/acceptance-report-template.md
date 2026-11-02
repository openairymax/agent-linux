<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# AirymaxOS v&lt;ver&gt; 验收报告

> 归档位置：`releases/v<ver>/acceptance-report.md`。本文件是模板，尖括号
> 占位符必须替换为实测值；任何占位符残留即视为未取证，不得归档、不得挂
> 发行 tag。
>
> 章节与六步规程（`0.0.4-极境OS任务方案.md` §14.1）的对应：§1 准备、
> §2 执行、§3 取证、§4–§5 判定、§6 发行自证、§8 复核与归档。
> 判定结果只取 `PASS` / `FAIL` / `WAIVED` / `BLOCKED` 四种；`WAIVED` 必须
> 写明理由与承接任务，`BLOCKED` 必须写明缺哪一侧证据。

## 1. 准备：环境快照

取证头三行，缺一行即 `BLOCKED`：

| 项 | 值 | 取证命令 |
|----|----|----------|
| ISO sha256 | `<sha256>` | `sha256sum airymaxos-<ver>-x86_64.iso` |
| 目标机 NVR | `<厂商> <型号> <BIOS 版本>` | `dmidecode -s system-product-name` / `-s bios-version` |
| 内核 `uname -r` | `<kernel-release>` | `uname -r` |

附加登记项（用于复现，不影响判定）：

| 项 | 值 |
|----|----|
| 构建提交 | `<sha>` |
| `image-build` run id | `<run id>` |
| 发行资产来源 | `<tag>` / Release `<id>` |
| 构建容器镜像摘要 | `<image@sha256>` |

判定：三行取证头任一与登记不符（摘要、机型、内核 release）即 `BLOCKED`。

## 2. 执行记录

| run / job | 起止（UTC） | 结论 | 日志链接 |
|-----------|-------------|------|----------|
| `<run id>` / assemble | `<start>` – `<end>` | `<PASS/FAIL>` | `<url>` |
| `<run id>` / accept（UEFI） | `<start>` – `<end>` | `<PASS/FAIL>` | `<url>` |
| `<run id>` / accept（BIOS） | `<start>` – `<end>` | `<PASS/FAIL>` | `<url>` |
| `<run id>` / accept（U 盘） | `<start>` – `<end>` | `<PASS/FAIL>` | `<url>` |

命令按任务卡逐条执行并原样记录，禁止合并或省略参数；被改写的命令其结论
不成立。

## 3. 取证：证据文件

每份证据必须有摘要与关键行，只有截图不构成证据：

| 证据 | sha256 | 关键行 | 归档路径 |
|------|--------|--------|----------|
| `airymaxos-<ver>-x86_64.iso` | `<sha256>` | — | `releases/v<ver>/` |
| `payload-manifest.txt` | `<sha256>` | `count=<n>`、内核包三行 | `releases/v<ver>/` |
| `image-ci-manifest.txt` | `<sha256>` | `stage iso=pass`、`result=pass` | `releases/v<ver>/` |
| `install-test-<run>.tap` | `<sha256>` | `1..<n>`、失败用例 | `releases/v<ver>/` |
| `serial-<run>.log` | `<sha256>` | 内核 `Linux version` 行 | `releases/v<ver>/` |
| `smoke.tap` | `<sha256>` | `AIRYMAX-SMOKE-PASS` | `releases/v<ver>/` |
| `flash-<dev>-<utc>.json` | `<sha256>` | 镜像摘要、盘序列号 | `releases/v<ver>/` |

## 4. 判定表（分层）

| 层 | 用例 / 门禁 | 结果 | 证据 | 备注 |
|----|-------------|------|------|------|
| L0 | 静态门禁（`ci/ci.sh distro-lint`） | `<PASS/FAIL>` | `<run url>` | |
| L1 | RPM / 内核 / ISO 组装 | `<PASS/FAIL>` | `image-ci-manifest.txt` | |
| L2 | 装机 + 冷启动（UEFI / BIOS / U 盘） | `<PASS/FAIL>` | `install-test-*.tap`、`serial-*.log` | 缺一条腿即 `BLOCKED` |
| L3 | UKUI 会话 smoke | `<PASS/FAIL>` | `smoke.tap` | 夹具未落地记 `BLOCKED`，不得发行 |

## 5. 发行自证（§14.2 五项）

缺任一项即 `BLOCKED`：

| # | 判据 | 证据 | 结果 |
|---|------|------|------|
| 1 | ISO `sha256` 与 `oras manifest fetch` 回读摘要一致 | `<oras 输出摘要>` | `<PASS/FAIL>` |
| 2 | ISO 内内核 RPM NEVRA 与本版 `%release` 逐字一致（J-13） | §6 对照表 | `<PASS/FAIL>` |
| 3 | 每个制品有 `.asc` 签名且 `gpg --verify` 通过（J-26） | `<gpg 输出>` | `<PASS/FAIL>` |
| 4 | 发布资产 `have == want`，旧代同名包已清理（J-15） | `<资产计数>` | `<PASS/FAIL>` |
| 5 | tag 指向的树即产出资产的树（J-16） | `<tag sha>` / `<run head_sha>` | `<PASS/FAIL>` |

## 6. ISO sha256 ↔ 内核 RPM NEVRA 对照（J-13 / QG-12）

两侧同时给出并逐字相等才算成立；缺任一侧即 `BLOCKED`。数据取自
`ci/ci.sh release-verify --iso-dir <dir> --payload <file> --kernel-rpms <dir>`：

| ISO | ISO sha256 | 内核主包 NEVRA | spec `airy_version` | spec `airy_release` | 判定 |
|-----|------------|----------------|---------------------|---------------------|------|
| `airymaxos-<ver>-x86_64.iso` | `<sha256>` | `airymaxos-kernel-<ver>-<rel>.x86_64` | `<ver>` | `<rel>` | `<PASS/FAIL>` |

门禁原始输出：

```text
<粘贴 release-verify 输出，含 iso 行与全部 rpm 行>
```

`%dist` 后缀（如 `-2.oe2403`）在 release 位允许出现，版本位必须逐字相等。

## 7. 已知问题与验收边界

| # | 现象 | 影响面 | 判定 | 承接任务 / 理由 |
|---|------|--------|------|-----------------|
| 1 | `<现象>` | `<影响>` | `<WAIVED/BLOCKED>` | `<任务编号>` |

## 8. 复核与归档

| 项 | 内容 |
|----|------|
| 复核人 | `<姓名> <邮箱>` |
| 复算命令 | `<逐条列出，第二人可据此重放>` |
| 复核结论 | `<PASS/FAIL>` |
| 归档路径 | `releases/v<ver>/` |
| 归档件清单 | ISO、`.sha256`、`payload-manifest.txt`、`image-ci-manifest.txt`、日志、本报告 |

复核要求：第二人按证据复算关键判据（ISO 摘要、NEVRA、资产计数），无法
复算即 `FAIL`；归档路径不在仓库内即 `FAIL`。
