#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/session-switch.sh — 运行期会话切换判据（S-1…S-9）
#
# 用法:
#   ci/gates/session-switch.sh
#
# 只在目标机上跑：需要 root、systemd、已安装的 airy-session-mode，以及一块
# 能真正切 VT 的显卡。由 V-02 的 tui-e2e.sh 调用，不新增独立 QG。开发机上
# 以退出码 5 报告「参数不满足」，不静默通过。
#
# 每个用例的通过条件都取自 §14.15 的表，判据一律用实测值与实测值比较：
# GUI 的 VT 号从 loginctl 的 TTY 列取（F-52），不写死 ttyN。
#
# S-5（忙碌拒绝）依赖 TUI 会话状态机 A-04；该卡未落盘时本脚本以 skip 记录
# 而不是伪造通过。A-04 交付后在此处补上「长任务在途 → rc=busy」的断言。
#
# 退出码: 0 全部通过；1 有判据失败；5 参数或环境不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly BIN=/usr/bin/airy-session-mode
readonly DIR=/run/airymaxos/session
readonly MARK=AIRYMAX-DESKTOP-READY
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

FAIL=0

mode()
{
    cat "${DIR}/mode" 2>/dev/null || echo none
}

active()
{
    systemctl is-active --quiet "$1"
}

fg()
{
    fgconsole 2>/dev/null || echo "?"
}

tty_of()
{
    loginctl list-sessions --no-legend 2>/dev/null |
        awk '$5 ~ /^tty[0-9]+$/ { print $5; exit }'
}

sid_of()
{
    loginctl list-sessions --no-legend 2>/dev/null |
        awk '$5 ~ /^tty[0-9]+$/ { print $1; exit }'
}

mark()
{
    journalctl -q -b -o cat -u airymax-desktop-ready.service 2>/dev/null |
        grep -c "${MARK}" || true
}

# wait_mode <目标> <秒>：等到实际态成为目标。
wait_mode()
{
    local left=$2

    while [ "${left}" -gt 0 ]; do
        [ "$(mode)" = "$1" ] && return 0
        sleep 1
        left=$((left - 1))
    done
    return 1
}

ok()
{
    echo "S-$1 ${2}: ok"
}

bad()
{
    echo "S-$1 ${2}: FAIL ${3:-}"
    FAIL=$((FAIL + 1))
}

# 前置：当前必须在 TUI，否则先切回来。
need_tui()
{
    [ "$(mode)" = tui ] && return 0
    "$BIN" tui >/dev/null 2>&1
    wait_mode tui 30
}

# 前置：当前必须在 GUI。
need_gui()
{
    [ "$(mode)" = gui ] && return 0
    "$BIN" gui >/dev/null 2>&1
    wait_mode gui 120
}

case_s1()
{
    local before after

    need_tui || { bad 1 幂等 "无法回到 tui"; return; }
    before=$(systemctl show -p ActiveEnterTimestamp boot-tui.service)
    if ! "$BIN" tui >/dev/null 2>&1; then
        bad 1 幂等 "rc 非 0"
        return
    fi
    after=$(systemctl show -p ActiveEnterTimestamp boot-tui.service)
    [ "${before}" = "${after}" ] || { bad 1 幂等 "会话被重启"; return; }
    ok 1 幂等
}

case_s2()
{
    local base tty

    base=$(mark)
    "$BIN" gui >/dev/null 2>&1 || { bad 2 TUI转GUI "提交失败"; return; }
    wait_mode gui 120 || { bad 2 TUI转GUI "mode 未变: $(mode)"; return; }
    [ "$(mark)" -gt "${base}" ] || { bad 2 TUI转GUI "无新就绪标记"; return; }
    active boot-tui.service && { bad 2 TUI转GUI "boot-tui 仍 active"; return; }
    tty=$(tty_of)
    [ -n "${tty}" ] || { bad 2 TUI转GUI "无座位 TTY"; return; }
    [ "$(fg)" = "${tty#tty}" ] || { bad 2 TUI转GUI "前台 $(fg)≠${tty#tty}"; return; }
    ok 2 TUI转GUI
}

case_s3()
{
    local pid tpgid

    need_gui || { bad 3 GUI转TUI "无法进入 gui"; return; }
    "$BIN" tui >/dev/null 2>&1 || { bad 3 GUI转TUI "提交失败"; return; }
    wait_mode tui 40 || { bad 3 GUI转TUI "mode 未变: $(mode)"; return; }
    active boot-tui.service || { bad 3 GUI转TUI "boot-tui 未 active"; return; }
    pid=$(systemctl show -p MainPID --value boot-tui.service)
    tpgid=$(ps -o tpgid= -p "${pid}" 2>/dev/null | tr -d ' ')
    [ "${tpgid}" = "${pid}" ] || { bad 3 GUI转TUI "tpgid ${tpgid}≠${pid}"; return; }
    [ "$(fg)" = 1 ] || { bad 3 GUI转TUI "前台 $(fg)≠1"; return; }
    ok 3 GUI转TUI
}

case_s4()
{
    local sid

    need_gui || { bad 4 注销回落 "无法进入 gui"; return; }
    sid=$(sid_of)
    [ -n "${sid}" ] || { bad 4 注销回落 "无会话"; return; }
    loginctl terminate-session "${sid}" >/dev/null 2>&1 ||
        { bad 4 注销回落 "注销失败"; return; }
    wait_mode tui 60 || { bad 4 注销回落 "未回 tui: $(mode)"; return; }
    journalctl -q -b -o cat -u airy-session-switch@tui.service |
        grep -q 'reason=logout' || { bad 4 注销回落 "无 reason=logout"; return; }
    ok 4 注销回落
}

case_s6()
{
    local conf=/etc/lightdm/lightdm.conf.d/50-airymax.conf
    local backup

    need_tui || { bad 6 回滚 "无法回到 tui"; return; }
    backup=$(mktemp)
    cp "${conf}" "${backup}" || { bad 6 回滚 "备份失败"; return; }
    sed -i 's|^xserver-command=.*|xserver-command=/bin/false|' "${conf}"
    "$BIN" gui >/dev/null 2>&1
    sleep 1
    if wait_mode gui 5; then
        cp "${backup}" "${conf}"
        rm -f "${backup}"
        bad 6 回滚 "故障注入未生效"
        return
    fi
    cp "${backup}" "${conf}"
    rm -f "${backup}"
    wait_mode tui 120 || { bad 6 回滚 "未回滚: $(mode)"; return; }
    [ "$(cat "${DIR}/desired" 2>/dev/null)" = tui ] ||
        { bad 6 回滚 "desired 未回滚"; return; }
    journalctl -q -b -o cat -u airy-session-switch@gui.service |
        grep -q 'switch=failed:' || { bad 6 回滚 "无 switch=failed"; return; }
    [ "$(fg)" = 1 ] || { bad 6 回滚 "前台 $(fg)≠1"; return; }
    ok 6 回滚
}

case_s7()
{
    local before rc

    before=$(journalctl -q -b -o cat -u airy-session-switch@gui.service |
        grep -c 'AIRYMAX-SESSION-MODE' || true)
    "$BIN" x >/dev/null 2>&1
    rc=$?
    [ "${rc}" -eq 2 ] || { bad 7 非法参数 "rc=${rc}"; return; }
    [ "$(journalctl -q -b -o cat -u airy-session-switch@gui.service |
        grep -c 'AIRYMAX-SESSION-MODE' || true)" = "${before}" ] ||
        { bad 7 非法参数 "有单元动作"; return; }
    ok 7 非法参数
}

case_s8()
{
    local base

    need_tui || { bad 8 授权 "无法回到 tui"; return; }
    base=$(journalctl -q -b -o cat -u airy-session-switch@gui.service |
        grep -c 'AIRYMAX-SESSION-MODE' || true)
    systemd-run --quiet --wait --pipe --property=User=amuser \
        --unit=airy-gate-auth "${BIN}" gui >/dev/null 2>&1 ||
        { bad 8 授权 "非 root 调用失败"; return; }
    systemctl reset-failed airy-gate-auth.service >/dev/null 2>&1
    wait_mode gui 120 || { bad 8 授权 "未切换（可能被 polkit 拒绝）"; return; }
    # oneshot 走完即 inactive，`is-active` 不成立；判据取「确实切过去了」。
    [ "$(journalctl -q -b -o cat -u airy-session-switch@gui.service |
        grep -c 'AIRYMAX-SESSION-MODE' || true)" -gt "${base}" ] ||
        { bad 8 授权 "无切换记录"; return; }
    if journalctl -q -b -o cat -u airy-gate-auth.service |
        grep -q 'not authorized'; then
        bad 8 授权 "出现 not authorized"
        return
    fi
    ok 8 授权
}

case_s9()
{
    rm -rf "${DIR}"
    need_gui || { bad 9 目录自建 "gui 方向失败"; return; }
    need_tui || { bad 9 目录自建 "tui 方向失败"; return; }
    local stat_line
    stat_line=$(stat -c '%a %U' "${DIR}" 2>/dev/null)
    [ "${stat_line}" = "755 root" ] ||
        { bad 9 目录自建 "${stat_line:-缺目录}"; return; }
    ok 9 目录自建
}

main()
{
    [ "$(id -u)" -eq 0 ] || { err "需要 root"; exit "${EINVAL}"; }
    [ -x "${BIN}" ] || { err "缺 ${BIN}"; exit "${EINVAL}"; }
    need_cmd systemctl journalctl loginctl fgconsole ps
    echo "S-5 忙碌拒绝: skip（依赖 A-04 会话状态机）"
    case_s1
    case_s2
    case_s3
    case_s4
    case_s6
    case_s7
    case_s8
    case_s9
    [ "${FAIL}" -eq 0 ] || { err "${FAIL} 条判据失败"; exit 1; }
    echo "session-switch: 全部通过"
}

main "$@"
