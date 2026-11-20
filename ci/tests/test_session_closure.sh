#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_session_closure.sh — 控制台判据的负向回归
#
# 控制台集合的事实源在 0.0.5 从安装器 --append 收敛到 boot/grub_args.sh 的
# consoles 常量（D-30）。两条判据只在「旧形态被写回来」时才该红：--append
# 重新夹带 console=，或 consoles 常量被改坏、删掉。真树常年为绿证明不了
# 判据还活着，因此这里在真树副本上逐项破坏，断言判据串确实出现。
#
# 用法: bash ci/tests/test_session_closure.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/session-closure.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

# fresh DIR —— 真树副本，用例之间互不污染。
fresh()
{
    rm -rf "$1"
    mkdir -p "$1"
    cp -a "${ROOT}/distro" "$1/distro"
}

# run DIR —— 对副本跑静态闭包，回显退出码，输出留在 $tmp/out。
run()
{
    bash "${GATE}" --source "$1" >"${tmp}/out" 2>&1
    printf '%s' "$?"
}

expect() { # expect <用例名> <判据串> <期望退出码>
    local rc
    rc="$3"
    if grep -qF -- "$2" "${tmp}/out"; then
        ok "$1"
    else
        bad "$1: 输出未出现「$2」（rc=${rc}）"
        sed -n '1,6p' "${tmp}/out"
    fi
}

# 基线：副本与真树一致时必须通过，否则后面的破坏无从归因。
fresh "${tmp}/base"
rc=$(run "${tmp}/base")
if [ "${rc}" = 0 ]; then
    ok "基线与真树一致"
else
    bad "基线判负（rc=${rc}）"
    sed -n '1,10p' "${tmp}/out"
fi

# 1) --append 重新夹带 console=：首启生效、下一次 grub2-mkconfig 丢掉，正是
#    D-30 之前的形态，顺序敏感项还会排到 tty0 之后抢走 /dev/console。
fresh "${tmp}/append"
python3 - "${tmp}/append/distro/kickstart/airymaxos-workstation.ks" <<'PY'
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
old = '--append="loglevel=5 plymouth.enable=0"'
new = '--append="loglevel=5 plymouth.enable=0 console=ttyS0,115200 console=tty0"'
p.write_text(p.read_text().replace(old, new, 1))
PY
rc=$(run "${tmp}/append")
[ "${rc}" = 1 ] || bad "append 夹带 console= 未判负（rc=${rc}）"
expect "append 夹带 console= 判在 kickstart/console-count" \
    "kickstart/console-count" "${rc}"

# 2) consoles 常量只剩默认集：ARM 上 ttyAMA0 缺失时串口没有 getty，无头
#    验收取不到任何一行，而这在 x86_64 上完全不可见。
fresh "${tmp}/arm"
python3 - "${tmp}/arm/distro/boot/grub_args.sh" <<'PY'
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
keep = [ln for ln in p.read_text().splitlines(True)
        if "consoles='ttyAMA0,115200 tty0'" not in ln]
p.write_text("".join(keep))
PY
rc=$(run "${tmp}/arm")
[ "${rc}" = 1 ] || bad "ARM consoles 缺失未判负（rc=${rc}）"
expect "ARM consoles 缺失判在 grub-args/consoles-arm" \
    "grub-args/consoles-arm" "${rc}"

if [ "${fails}" -ne 0 ]; then
    printf '%d 项失败\n' "${fails}"
    exit 1
fi
printf '全部通过\n'
