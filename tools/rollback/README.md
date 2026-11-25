<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# airy-rollback — AirymaxOS 版本级回滚编排器

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

AirymaxOS 的升级必须可回退。按影响范围由小到大，系统提供三层回滚：
L1 回滚 dnf 事务、L2 重载并重启 daemon、L3 切换根分区快照。手工执行时
容易漏掉前置检查、层次判断与审计留痕，导致回滚本身成为新的故障源。

本工具以 [manifest.yaml](manifest.yaml) 为**唯一事实源（SSoT）**：路径、
12 个 daemon unit 与全部外部命令模板只在该清单定义；代码只负责清单校验、
快照选择、层次决策、前置检查、步骤执行与审计，不复制任何命令。回滚后
必须通过 12 daemon 与记忆卷校验，失败即返回非零退出码。

工具**不模拟任何系统状态**：所有检查读取真实文件、挂载信息与 systemd
状态，所有执行动作都是真实外部命令。

权威依据：

| 内容 | 出处 |
|------|------|
| L1/L2/L3 回滚模型、命令契约 | `docs/docs-linux/100-operations/01-deployment.md` §11 |
| 快照保留 7 天（OS-OPS-021） | 同上 §11 |
| 回滚后校验 12 daemon 与记忆卷（OS-OPS-022） | 同上 §11 |
| dnf 回滚不覆盖 `%config(noreplace)`（OS-OPS-023） | 同上 §11 |
| 记忆卷独立挂载（OS-OPS-036） | 同上 §11 |
| 升级标记 `.pending-upgrade` 生命周期 | `docs/docs-linux/120-development-process/09-release-process.md` §7.2-7.5 |
| 12 个 daemon unit 名单与顺序 | `docs/docs-linux/100-operations/07-systemd-integration.md` §8.1 |
| Python 类型注解、数据类、错误码 | `docs/docs-linux/50-engineering-standards/10-coding-style/scripting_coding_style.md` |

## 回滚层次

| 层次 | 触发条件（`--layer auto`） | 动作 | 是否需要重启 |
|------|---------------------------|------|--------------|
| L1 | 快照含 dnf 事务且内核、daemon 集合一致 | `dnf history rollback` + daemon 重载/重启 | 否 |
| L2 | 无 dnf 事务，仅 unit 状态变化 | daemon 重载/重启 | 否 |
| L3 | 内核不同、daemon 集合不同或操作者指定 | btrfs `set-default` 或 LVM `merge` | 是 |

L1 不会回滚标记为 `%config(noreplace)` 的配置文件（OS-OPS-023），命令
输出会显式提示。L3 会先为当前根子卷创建 `pre-rollback-<时间戳>` 快照，
再切换默认子卷；`--no-reboot` 供维护窗口内分批操作。

## 子命令

| 子命令 | 作用 | 是否修改系统 |
|--------|------|--------------|
| `check` | 校验清单 schema 与命令模板契约 | 否 |
| `plan` | 只读生成计划并执行前置检查 | 否 |
| `apply` | 执行回滚（默认子命令） | 是 |
| `verify` | 校验 12 daemon、记忆卷与升级标记 | 否 |
| `prune` | 删除超过保留期的 L3 快照 | 是 |

## 使用方法

```bash
# 校验清单
python3 tools/airy-rollback/airy_rollback.py check

# 生成回滚到上一版本的计划
python3 tools/airy-rollback/airy_rollback.py plan

# 回滚到指定版本 / 指定快照
python3 tools/airy-rollback/airy_rollback.py apply --to 0.0.2
python3 tools/airy-rollback/airy_rollback.py apply --snapshot pre-rollback-20260101T000000Z

# 只做前置检查，不执行命令
python3 tools/airy-rollback/airy_rollback.py apply --dry-run

# 强制 L3 且不自动重启
python3 tools/airy-rollback/airy_rollback.py apply --layer l3 --no-reboot

# 回滚后校验
python3 tools/airy-rollback/airy_rollback.py verify

# 清理过期快照（先演练）
python3 tools/airy-rollback/airy_rollback.py prune --dry-run
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 成功 |
| `1` | 步骤执行失败（无步骤生效） |
| `2` | 用法或清单错误 |
| `3` | 前置检查未通过 |
| `4` | 目标版本 / 快照不存在 |
| `5` | 快照元数据损坏 |
| `6` | 回滚后校验未通过 |
| `7` | 另一个回滚正在进行（锁冲突） |
| `8` | 部分成功：已有步骤生效，需人工复核 |

### 审计

所有 `apply` / `prune` / `verify` 都向清单 `audit_log` 追加一行 JSON，
包含时间、操作者、层次、目标、退出码、结果、注释、命令序列与检查项。
文件以 `O_APPEND` 打开并 `fsync`，权限为 `0640`。

### 快照元数据

每个 L3 快照目录必须包含 `rollback.json`：

```json
{
  "version": "0.0.2",
  "created": "2026-01-01T00:00:00Z",
  "kind": "btrfs",
  "source": "/@snapshots/20260101",
  "kernel": "6.6.0-airymax",
  "daemons": ["agentrt-gateway.service", "..."],
  "dnf_tx": 42
}
```

`dnf_tx` 可省略；`kind` 仅接受 `btrfs` / `lvm`；`created` 必须是
ISO-8601 时间。元数据缺失的快照目录会被忽略，元数据损坏会被记录并在
按名选择时返回退出码 `5`。

## 维护清单

- **新增 / 调整命令**：只改 `manifest.yaml` 的 `commands`，同步更新
  `airy_rollback.py` 的 `REQUIRED_CMDS` 与 `CMD_ARGS`；不得在代码内写死
  命令或路径。
- **daemon 名单变化**：同步 `manifest.yaml`、`07-systemd-integration.md`
  §8.1 与 `DAEMON_COUNT` 校验值，三处必须一致。
- **保留期调整**：修改 `retention_days` 并同步 OS-OPS-021 的文档取值。
- **新增层次**：必须同时更新 `Layer`、`pick_layer`、`build_steps`、
  README 与测试，避免只改一侧造成语义漂移。

## CI 接入

- **管理仓 `os-chk` 注册表**：以门禁 `OS-OPS-ROLLBACK`（P0 /
  category `build`）登记，由 `tools/os-chk/os_chk.py --check` 统一编排。
- **`os-chk-verify.yml`**：`paths` 覆盖本目录，安装 `requirements-dev.txt`
  并运行 pytest。

## 单元测试

```bash
pip install -r tools/airy-rollback/requirements-dev.txt
python3 -m pytest tools/airy-rollback/tests/ -q
```

测试以真实清单为基线，在临时目录合成快照、版本文件与审计目录，通过受控
执行器逐条断言层次决策、前置检查、失败短路、部分成功、L3 重启分支、
审计落盘与 CLI 退出码；不依赖宿主 systemd 或真实根分区。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
