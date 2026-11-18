#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/tui-gate.sh — TUI 门禁共用的钉版校验与 cargo 调用
#
# tui-check 与 tui-frame 曾各抄一份「取实际编译器版本、断言与钉版一致」
# 和「产物落在 TMPDIR、源码树不留 target/」，再加上逐行相同的根目录推导
# 与 Cargo.lock 校验。复制出来的判据不会同步演化：钉版一改只改一处，另一
# 份就按旧版本放行或误拒。判定只有一份，落点也只能有一处。
#
# 调用约定：先 source ci/lib/log.sh（依赖 err / die / need_dir），对本文件
# source 之后调用 tui_setup，并把本门禁脚本自身的路径作为第一个参数。
# tui_setup 回填 TUIDIR，其余函数直接引用它。
# ===========================================================================

readonly TUI=system/tui
readonly PIN=1.90.0

# 钉版校验：rustup 读到工作区里的 rust-toolchain.toml 后自行取版本并装
# components，本函数只断言「实际生效的」编译器就是钉住的那个。
pin_check()
{
    local got

    need_cmd cargo rustc awk
    got="$(cd "${TUIDIR}" && rustc --version 2>/dev/null | awk '{print $2}')"
    [ "${got}" = "${PIN}" ] ||
        die "编译器 ${got:-未知} 与钉版 ${PIN} 不一致（见 ${TUI}/rust-toolchain.toml）"
}

# 产物落在 TMPDIR 下的独立目标目录，不在源码树里留 target/。
cargo_run()
{
    local tgt="${TMPDIR:-/tmp}/airy-tui-target"

    ( cd "${TUIDIR}" && CARGO_TARGET_DIR="${tgt}" cargo "$@" )
}

# tui_root OPT DIR —— 记录 `--root` 的取值。赋值留在本文件，门禁只传参：
# 变量由本文件回读，各门禁不必为了「赋值后未使用」再写一行 export。
tui_root()
{
    need_dir "$1" "$2"
    ROOT="$2"
}

# tui_setup SELF —— 推导根目录、校验工作区可构建并核对编译器钉版。
tui_setup()
{
    local here="$1"

    [ -n "${ROOT:-}" ] ||
        ROOT="$(cd "$(dirname "${here}")/../.." && pwd -P)"
    TUIDIR="${ROOT}/${TUI}"
    need_dir "${TUI}" "${TUIDIR}"
    [ -f "${TUIDIR}/Cargo.lock" ] || die "缺少 ${TUI}/Cargo.lock"
    pin_check
}
