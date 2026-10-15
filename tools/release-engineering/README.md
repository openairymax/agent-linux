<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# release-engineering — AirymaxOS release 工程工具集

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

本目录承接 L-16 release 工程能力，当前包含两个工具：

| 工具 | 职责 |
|------|------|
| [`merge_sbom.py`](#merge_sbompy--spdx-sbom-分片合并) | 把各子仓扫描出的 SPDX 2.3 SBOM 分片合并为单一 `agent-linux` 主体物料清单 |
| [`release_manifest.py`](#release_manifestpy--发布物料清单门禁与生成) | 以 `manifest.yaml` 为唯一事实源，校验「物料清单 ↔ release 流水线」双向一致，并生成校验和与 release notes |

两个工具都**只做真实计算**，不做任何占位跳过：冲突即失败，绝不静默丢弃或覆盖。

---

## merge_sbom.py — SPDX SBOM 分片合并

### 用途

release 流水线以 `syft` 为 kernel 与 7 个用户态子仓分别生成分片，本工具负责把
它们合并为符合
[06-sbom-generation.md](../../../docs/docs-linux/190-distribution/06-sbom-generation.md)
§2 的文档。工具**只做字段级真实合并**，不做任何占位跳过：同一去重键在两份分片中
内容不一致时判冲突并失败，绝不静默丢弃或覆盖，避免合并出「看似正常」的错误 SBOM。

权威依据：

| 内容 | 出处 |
|------|------|
| SPDX 2.3 文档必需字段（`spdxVersion` / `dataLicense` / `SPDXID` / `name` / `documentNamespace` / `creationInfo` / `packages` / `relationships`） | `docs/docs-linux/190-distribution/06-sbom-generation.md` §2.1.1 |
| 文档级固定取值（`SPDX-2.3` / `CC0-1.0` / `SPDXRef-DOCUMENT`） | 同上 §2.1.3 |
| 四类关系（`DESCRIBES` / `CONTAINS` / `DEPENDS_ON` / `GENERATED_FROM`） | 同上 §2.3.1 |
| 可重复构建（`created` 由 `SOURCE_DATE_EPOCH` 派生） | 同上 §8 |
| 12 项发布物料清单与 SBOM 交付 | `docs/docs-linux/120-development-process/09-release-process.md` §3 |

### 合并规则

| # | 规则 | 说明 |
|---|------|------|
| 1 | 文档级固定字段 | `spdxVersion` 必须为 `SPDX-2.3`、`dataLicense` 必须为 `CC0-1.0`；任一分片不符即失败 |
| 2 | `packages` / `files` | 按 `SPDXID` 去重；同 ID 内容不一致判冲突 |
| 3 | `relationships` | 按 `(spdxElementId, relationshipType, relatedSpdxElement)` 三元组去重 |
| 4 | `externalDocumentRefs` / `hasExtractedLicensingInfos` | 分别按 `externalDocumentId` / `licenseId` 去重 |
| 5 | `creationInfo.creators` | 取并集并追加本工具标识 |
| 6 | 输出确定性 | `sort_keys=True`；包/文件按 `SPDXID` 排序、关系按三元组排序；`created` 由 `SOURCE_DATE_EPOCH` 派生 |

### 使用方法

```bash
# 合并 kernel + 7 个用户态子仓的 SPDX 分片
python3 tools/release-engineering/merge_sbom.py \
  --name "agent-linux-v0.3.0" \
  --namespace "https://airymaxos.com/spdx/agent-linux-v0.3.0" \
  --output sbom-agent-linux-v0.3.0.spdx.json \
  sbom-kernel.spdx.json sbom-memory.spdx.json sbom-security.spdx.json \
  sbom-cognition.spdx.json sbom-services.spdx.json sbom-system.spdx.json \
  sbom-cloudnative.spdx.json sbom-tests-linux.spdx.json

# 可重复构建：由提交时间派生时间戳
SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct) \
  python3 tools/release-engineering/merge_sbom.py \
  --output sbom.spdx.json sbom-*.spdx.json
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 合并成功 |
| `1` | 内容冲突或 SPDX 语义非法（版本/许可不一致、同键内容冲突等） |
| `2` | 用法 / 解析错误（缺少输入、文件不可读、非 JSON、`SOURCE_DATE_EPOCH` 非法等） |

---

## release_manifest.py — 发布物料清单门禁与生成

### 用途

[`manifest.yaml`](manifest.yaml) 是 AirymaxOS 发布物料的**唯一事实源**（SSoT），
逐项登记每类物料的格式、签名方式、权威依据、交付方式、证据路径与（未启用时的）
延后目标。本工具以它为输入，提供三种能力：

1. **门禁（`--check`）**：校验 `manifest.yaml` schema，并做「物料清单 ↔
   [release.yml](../../.github/workflows/release.yml)」**双向一致性**检查——正向要求
   每条 active 物料的 `evidence` 必须被流水线真实声明；反向要求流水线声明的每个产物
   glob 都已在清单登记。任一侧缺漏即失败，杜绝「文档写了、流水线没产」或
   「流水线产了、清单没登记」的漂移。
2. **校验和（`--checksums`）**：对指定文件流式计算 SHA-256，输出
   `<hash>  <filename>` 双空格格式（`sha256sum` 兼容）。
3. **release notes（`--notes`）**：按提交区间生成八要素 Markdown（版本概述 /
   新特性 / 修复的缺陷 / 安全修复 / 已知问题 / 升级说明 / 贡献者列表 /
   物料校验和），提交按 `CVE`/`security` → 安全修复、`fix` → 修复、其余 → 新特性归类。

工具**不做任何静默降级**：清单缺字段、流水线无产物、待校验文件缺失均以非零退出码
显式失败。

权威依据：

| 内容 | 出处 |
|------|------|
| 12 项发布物料清单（§3.1） | `docs/docs-linux/120-development-process/09-release-process.md` §3.1 |
| 源码 tarball 与校验和（§3.2） | 同上 §3.2 |
| RPM 包集合（§3.3） | 同上 §3.3 |
| SBOM 交付格式 | `docs/docs-linux/190-distribution/06-sbom-generation.md` §2 |
| release notes 八要素（§3.7） | `docs/docs-linux/120-development-process/09-release-process.md` §3.7 |
| SDK tarball / OCI 镜像 / DNF 仓库发布流水线 | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` |

### 清单结构

`manifest.yaml` 顶层仅两个键：`version`（清单 schema 版本）与 `materials`（物料列表）。
每条物料字段：

| 字段 | 含义 |
|------|------|
| `id` | 物料标识（唯一，`[a-z0-9-]+`） |
| `name` | 人类可读名称 |
| `format` | 交付格式（如 `RPM`、`SPDX 2.3 JSON`） |
| `signature` | 签名方案（如 `GPG`、`cosign`、`none`） |
| `authority` | 权威依据（文档路径 §小节） |
| `delivery` | `active`（已由流水线交付）/ `deferred`（尚未启用） |
| `evidence` | active 必填：流水线声明的产物 glob 列表 |
| `defer_reason` / `defer_target` | deferred 必填：延后原因与计划承接的里程碑 |

### 使用方法

```bash
# CI 精简门禁（PASS/FAIL 单行）
python3 tools/release-engineering/release_manifest.py --check

# 本地详细报表（默认模式，逐项列出物料与检查结果）
python3 tools/release-engineering/release_manifest.py

# 生成文件校验和
python3 tools/release-engineering/release_manifest.py --checksums \
  dist/rpm/x86_64/airymaxos-kernel-6.6.157-1.x86_64.rpm

# 生成 release notes
python3 tools/release-engineering/release_manifest.py --notes \
  --version v0.0.3 --rev-range HEAD~20..HEAD

# 指定非默认清单
python3 tools/release-engineering/release_manifest.py --check \
  --manifest tools/release-engineering/manifest.yaml
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 检查通过 / 生成成功 |
| `1` | 门禁失败（schema 违规、清单 ↔ 流水线不一致） |
| `2` | 用法 / 输入错误（清单不可读、YAML 非法、待校验文件缺失、git 不可用等） |

### 维护清单

- **新增流水线产物**时，须在 `manifest.yaml` 新增对应物料条目并把 `evidence` 指向
  该产物 glob，否则门禁反向检查判「未登记物料」失败。
- **命名规则（SSoT）**：发布资产（tarball / SBOM / 校验和 / release notes 及其签名）
  统一用 `agent-linux-<ver>` 前缀（09-release-process.md §3.2）；RPM 包名统一用
  `airymaxos-*`（§3.3）。二者为「资产名 vs 包名」并存的约定，不得混用。
- **启用 deferred 物料**时，把 `delivery` 改为 `active`、补 `evidence`、删 `defer_*`。
- 提交类落款按 [git-commit-message.md](../../../.trae/rules/git-commit-message.md)。

---

## CI 接入

- **管理仓 `release.yml`（`build-and-sign` job）**：以
  `anchore/sbom-action/download-syft` 安装 `syft`，由 `git log -1 --pretty=%ct`
  计算 `SOURCE_DATE_EPOCH`，随后以新增 step 直接调用 `merge_sbom.py` 合并分片；
  已移除原「`if [ -f tools/merge-sbom.py ]` … `echo "SKIP"`」的存在性守卫桩路径。
- **管理仓 `release.yml`（物料 8 / 物料 9）**：在制品签名后以新增 step 调用
  `release_manifest.py --checksums` 产出 `agent-linux-<ver>.sha256sum`、以
  `--notes` 产出 `agent-linux-<ver>-release-notes.md`，并对二者
  `gpg --detach-sign --armor`；两份元数据经 `upload-artifact` 上载，并由
  `action-gh-release` 的 `body_path` 作为 Release 正文（取代 `generate_release_notes`）。
  因此 `manifest.yaml` 中 `checksums` / `release-notes` 已于 0.0.3 转为 `active`。
- **管理仓 `os-chk-verify.yml`**：调用 `release_manifest.py --check` 作为发布物料
  一致性门禁（登记于 `tools/os-chk/gates.yaml`）。
- 遵守管理仓「每 workflow ≤ 2 job」上限：仅新增 step，不新增 job。

## 单元测试

```bash
pip install -r tools/release-engineering/requirements-dev.txt
python3 -m pytest tools/release-engineering/tests/ -q
```

- `tests/test_merge_sbom.py` — 合并 / 去重 / 冲突 / 确定性 / CLI。
- `tests/test_release_manifest.py` — 清单 schema / 流水线产物提取 / 双向一致性 /
  校验和 / release notes / CLI / 真实仓库。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
