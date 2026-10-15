<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# release-engineering — AirymaxOS release 工程工具集

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

L-16 release 工程把各子仓扫描出的 **SPDX 2.3 SBOM 分片**合并为单一
`agent-linux` 主体物料清单。release 流水线以 `syft` 为 kernel 与 7 个用户态
子仓分别生成分片，本目录的 `merge_sbom.py` 负责把它们合并为符合
[06-sbom-generation.md](../../../docs/docs-linux/190-distribution/06-sbom-generation.md)
§2 的文档。

工具**只做字段级真实合并**，不做任何占位跳过：同一去重键在两份分片中内容
不一致时判冲突并失败，绝不静默丢弃或覆盖，避免合并出「看似正常」的错误 SBOM。

权威依据：

| 内容 | 出处 |
|------|------|
| SPDX 2.3 文档必需字段（`spdxVersion` / `dataLicense` / `SPDXID` / `name` / `documentNamespace` / `creationInfo` / `packages` / `relationships`） | `docs/docs-linux/190-distribution/06-sbom-generation.md` §2.1.1 |
| 文档级固定取值（`SPDX-2.3` / `CC0-1.0` / `SPDXRef-DOCUMENT`） | 同上 §2.1.3 |
| 四类关系（`DESCRIBES` / `CONTAINS` / `DEPENDS_ON` / `GENERATED_FROM`） | 同上 §2.3.1 |
| 可重复构建（`created` 由 `SOURCE_DATE_EPOCH` 派生） | 同上 §8 |
| 12 项发布物料清单与 SBOM 交付 | `docs/docs-linux/120-development-process/09-release-process.md` §3 |

## 合并规则

| # | 规则 | 说明 |
|---|------|------|
| 1 | 文档级固定字段 | `spdxVersion` 必须为 `SPDX-2.3`、`dataLicense` 必须为 `CC0-1.0`；任一分片不符即失败 |
| 2 | `packages` / `files` | 按 `SPDXID` 去重；同 ID 内容不一致判冲突 |
| 3 | `relationships` | 按 `(spdxElementId, relationshipType, relatedSpdxElement)` 三元组去重 |
| 4 | `externalDocumentRefs` / `hasExtractedLicensingInfos` | 分别按 `externalDocumentId` / `licenseId` 去重 |
| 5 | `creationInfo.creators` | 取并集并追加本工具标识 |
| 6 | 输出确定性 | `sort_keys=True`；包/文件按 `SPDXID` 排序、关系按三元组排序；`created` 由 `SOURCE_DATE_EPOCH` 派生 |

## 使用方法

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

## 维护清单

新增或调整被扫描的子仓时，需同步三处：本工具（无需改代码，仅输入参数）、
[release.yml](../../.github/workflows/release.yml) 的分片生成循环、以及
[06-sbom-generation.md](../../../docs/docs-linux/190-distribution/06-sbom-generation.md)
§2.4 的子仓登记。

## CI 接入

- **管理仓 `release.yml`（`build-and-sign` job）**：以
  `anchore/sbom-action/download-syft` 安装 `syft`，由 `git log -1 --pretty=%ct`
  计算 `SOURCE_DATE_EPOCH`，随后以新增 step 直接调用本工具合并分片；已移除
  原「`if [ -f tools/merge-sbom.py ]` … `echo "SKIP"`」的存在性守卫桩路径。
- 遵守管理仓「每 workflow ≤ 2 job」上限：仅新增 step，不新增 job。

## 单元测试

```bash
pip install -r tools/release-engineering/requirements-dev.txt
python3 -m pytest tools/release-engineering/tests/ -q
```

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
