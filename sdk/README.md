Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

# `sdk/` — 用户态语言绑定

AirymaxOS 的 A-IPC 与 syscall 面在四种语言下的常量与函数声明。**本目录所有
源文件都是生成物**，由 `tools/codegen/` 从契约源产出；手改会在下一次生成时
被覆盖，且零漂移门禁会直接判负。

## 目录

| 语言 | 文件 | 生成器 |
|------|------|--------|
| Go | `go/airyx/ipc.go`、`go/airyx/syscalls.go` | `ipc_gen.py`、`syscall_gen.py` |
| Python | `python/airyx/cap_ids.py`、`python/airyx/ipc.py`、`python/airyx/syscalls.py` | `cap_gen.py`、`ipc_gen.py`、`syscall_gen.py` |
| Rust | `rust/airyx/cap_ids.rs`、`rust/airyx/ipc.rs`、`rust/airyx/syscalls.rs` | `cap_gen.py`、`ipc_gen.py`、`syscall_gen.py` |
| TypeScript | `ts/airyx/ipc.ts`、`ts/airyx/syscalls.ts` | `ipc_gen.py`、`syscall_gen.py` |

能力编号只有 Rust 与 Python 两份：这两种语言的绑定需要常量集合参与类型检查，
另外两种由 syscall 声明带出即可，多一份副本只会多一处漂移面。

## 重新生成

```bash
python3 tools/codegen/run_codegen.py          # 按契约源重写本目录
python3 tools/codegen/run_codegen.py --check  # 零漂移门禁：只比对，不写盘
```

`--check` 是 CI 里的判据（IRON-9）。改契约的流程是「改契约源 → 生成 → 提交
生成物」，不是「改生成物」。

## 验证

生成一致不等于能编译：常量类型、保留字与模块边界只有在编译器里才会暴露。
`sdk-verify` 流水线把四种语言各放一个作业，判据实现在
`ci/gates/sdk-verify.sh`：

```bash
ci/gates/sdk-verify.sh --lang go|python|rust|ts
```

四份绑定都不带包清单（无 `go.mod`、`Cargo.toml`、`package.json`、`setup.py`）：
它们是随发行物交付的源码片段，不是可独立安装的库。门禁因此按每种语言的最小
编译形式判定——Go 在临时模块里 `vet` 加 `build`，Python 走 `py_compile`，
Rust 逐文件 `--emit=metadata`，TypeScript 走 `tsc --noEmit --strict`。

## 许可

双许可 AGPL-3.0-or-later 与 Apache-2.0，见仓库根 [LICENSE](../LICENSE) 与
[NOTICE](../NOTICE)。
