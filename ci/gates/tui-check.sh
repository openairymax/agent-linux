#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/tui-check.sh — TUI 工作区的编译期判据
#
# 用法:
#   ci/gates/tui-check.sh --kind fmt|clippy|test|cross [--target TRIPLE]
#
# 格式、静态检查与单元测试此前只以文档里的 cargo 命令存在：shell 与
# python 的门禁「本地能跑 = CI 能跑」，rust 这一侧没有对应入口。收进本
# 门禁之后，开发者与 runner 执行的是同一条命令，判据也只有一份。
#
# 工具链版本不在这里再声明一次：system/tui/rust-toolchain.toml 是唯一
# 事实源，rustup 按它选版本并装齐 components。本门禁只断言实际生效的
# 编译器与钉版一致——发行版 cargo 没有 rustup 时直接判错，避免换一套
# 编译器跑出「取决于机器」的结论。
#
# 所有 cargo 调用都带 --offline 与 --locked：离线源由 rust-vendor 门禁
# 保证，落在白名单之外的依赖在无网环境里第一步就失败。产物写到 TMPDIR
# 下的独立目标目录，不在源码树里留 target/。
#
# cross 只做类型检查：链接器不在本门禁的判据内，而跨架构的 cfg 分支与
# 类型错误在 check 阶段已经暴露；真机行为由装机验收承担。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly KINDS="fmt clippy test cross"
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/tui-gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/tui-gate.sh"

KIND=""
TARGET=""

usage()
{
    cat <<'EOF'
用法: ci/gates/tui-check.sh --kind fmt|clippy|test|cross [--target TRIPLE]
EOF
}

stage_fmt()
{
    cargo_run fmt --all --check
}

stage_clippy()
{
    cargo_run clippy --offline --locked --workspace --all-targets -- -D warnings
}

stage_test()
{
    cargo_run test --offline --locked --workspace --all-targets
}

stage_cross()
{
    need_cmd rustup
    rustup target add --toolchain "${PIN}" "${TARGET}" >/dev/null ||
        die "无法安装目标 ${TARGET}（需要 rustup 与对应预编译标准库）"
    cargo_run check --offline --locked --workspace --all-targets --target "${TARGET}"
}

run_kind()
{
    case "${KIND}" in
        fmt) stage_fmt ;;
        clippy) stage_clippy ;;
        test) stage_test ;;
        cross) stage_cross ;;
    esac
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --kind) KIND="${2:-}"; shift 2 ;;
        --target) TARGET="${2:-}"; shift 2 ;;
        --root) tui_root "$1" "${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${KIND}" ] || { usage >&2; exit "${EINVAL}"; }
case " ${KINDS} " in
    *" ${KIND} "*) ;;
    *) err "--kind 只接受 ${KINDS}"; exit "${EINVAL}" ;;
esac
if [ "${KIND}" = cross ] && [ -z "${TARGET}" ]; then
    err "cross 需要 --target TRIPLE"
    exit "${EINVAL}"
fi

tui_setup "${BASH_SOURCE[0]}"

group "tui: ${KIND}${TARGET:+ ${TARGET}}"
if ! run_kind; then
    endgroup
    die "TUI 判据未通过: ${KIND}"
fi
endgroup
say "TUI ${KIND} 通过"
