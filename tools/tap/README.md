<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# tools/tap — TAP/KTAP 证据工具链

`tap/` 收纳「把测试输出变成判据」的脚本：输入是测试框架原样吐出的 TAP 或嵌套
KTAP 文本，输出是计数摘要与退出码。它们不运行测试、不构建内核、不探测环境，
因此同一条命令在开发机、QEMU farm 与 CI runner 上给出同一结论。

| 文件 | 判据 | 权威条款 | 调用方 |
|------|------|----------|--------|
| `parse-tap.py` | 统计 `ok` / `not ok` 与计划行；`--fail-on-not-ok` 时存在 `not ok` 即失败 | OS-TEST-013~022 | `ci/gates/kernel-kselftest.sh` |
| `kunit-tap-diff.py` | 叶子用例数只增不减、基线套件不得消失、不得新增 `not ok` | OS-TEST-012 | `ci/gates/kernel-kunit.sh` |
| `kunit-tap-baseline.tap` | KUnit 基线快照（棘轮），由 `--update` 规范化写入 | OS-TEST-012 | `kunit-tap-diff.py --baseline-file` |

## 用法

```bash
# kselftest：解析 TAP，出现 not ok 即失败
python3 tools/tap/parse-tap.py --input kselftest.tap --fail-on-not-ok

# KUnit：与仓库基线比对（默认读 stdin，也可 --current FILE）
python3 tools/tap/kunit-tap-diff.py \
    --baseline-file tools/tap/kunit-tap-baseline.tap --current kunit.tap
```

## 基线的下调流程

`kunit-tap-baseline.tap` 是棘轮，不是快照文件。用例总数回退、套件被删除、新增
`not ok`，三者任一命中即失败。确需下调时必须在同一次变更里显式重写基线：

```bash
python3 tools/tap/kunit-tap-diff.py --current kunit.tap --update
```

`--update` 只保留子测试头与结果行，丢弃栈回溯、bench 计时、版本串等内核噪声，
因此对同一批用例重复写入得到逐字节一致的文件——评审 diff 里只会出现真实的
用例增减，不会被噪声淹没。

## 退出码

| 脚本 | 0 | 1 | 2 |
|------|---|---|---|
| `parse-tap.py` | 解析成功且无 `not ok` | 存在 `not ok`（仅在 `--fail-on-not-ok` 时） | 输入不可读 |
| `kunit-tap-diff.py` | 通过（用例数持平或增长） | 门禁失败 | 输入为空、基线缺失或不可解析 |

## 新增工具

放入本目录前先回答：它的判据是否只取决于一段 TAP/KTAP 文本？如果否（需要启动
虚拟机、需要编译、需要联网），它属于 `tools/airy-qemu/` 或 `ci/gates/`，而不是
这里。
