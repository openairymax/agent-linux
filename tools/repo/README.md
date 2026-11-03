<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# tools/repo — 仓库级治理 CLI

`repo/` 收纳只读「已入库状态」的治理工具：它们不碰构建产物、不启容器、不联网，
输入永远是工作树里的文件，输出只有退出码与一份可供人读的差异。这类判据必须能
在开发者本机与 CI 上给出同一结论，因此不做任何环境探测。

| 脚本 | 判据 | 权威条款 | 调用方 |
|------|------|----------|--------|
| `validate-ssot.py` | 文档中出现的每个 `OS-*-NNN` / `*-NNN` 规则 ID 都已在 `ssot-registry.yaml` 注册（区间写法逐个展开比对） | OS-IRON-015 | `ci/gates/ssot-verify.sh`、`ssot-verify.yml` |
| `check-copyright.py` | 仓库自有 `.md` 文件首行带 `Copyright` 或 `SPDX-License-Identifier` | OS-STD-DOC | `ci/gates/mgmt-leaves.sh` |
| `aggregate-subrepo-ci.py` | 叶子仓 CI 状态聚合；子仓清单从 `.gitmodules` 派生，不手工维护第二份 | OS-STD-GOV | `ci/gates/mgmt-leaves.sh`、`mgmt-verify.yml` |
| `codeowners.py` | `CODEOWNERS` 是 `MAINTAINERS` 的投影：路径取 `F:`、所有者取 `O:`；`--check` 比对并输出统一 diff | OS-STD-GOV-004 | `make codeowners-check`、`ci/gates/mgmt-files.sh` |

## codeowners.py 的两个约定

**只认 `O:`。** `M:`/`R:` 是邮件地址，GitHub 不接受这种 owner 写法；早期脚本
把邮箱拼成 `@user@host`，产出的是语法合法但无人认领的条目。现在所有者必须写成
GitHub 句柄，并且每个含 `F:` 的块都要有 `O:`，缺了直接以退出码 2 失败——宁可
拒绝生成，也不留下一条谁也管不到的路径。

**生成即校验。** 默认模式把投影打到标准输出（`make codeowners-sync` 重定向写入），
`--check` 与原文件逐字节比对；两者共用同一个渲染函数，因此不存在「生成器和校验
器口径不同」的可能。

## 退出码

| 脚本 | 0 | 1 | 2 |
|------|---|---|---|
| `validate-ssot.py` | 全部已注册（允许弃用告警） | 存在未注册 ID | YAML 解析或文件错误 |
| `check-copyright.py` | 全部合规 | 存在第 1 行缺版权头的 `.md` | `--root` 不存在，或无法枚举被跟踪文件 |
| `aggregate-subrepo-ci.py` | 聚合完成（无 `--fail-on-red`，或全绿）；无凭据时打印 `SKIP` 后 0 | `--fail-on-red` 且存在红子仓 | 无法解析 `git submodule status` |
| `codeowners.py` | 已打印投影，或 `--check` 无漂移 | `--check` 发现漂移 | 解析失败、路径非法、文件缺失 |

## 新增工具

放入本目录前先回答一个问题：它的结论是否只取决于工作树？如果否（需要编译、
需要 QEMU、需要联网），它属于 `ci/gates/` 或对应的工具目录，而不是这里。
