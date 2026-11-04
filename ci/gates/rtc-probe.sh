#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/rtc-probe.sh — 装机 RTC 写入的限时与取证门禁（I-28 / D-34）
#
# 用法:
#   ci/gates/rtc-probe.sh [--tree DIR]
#   ci/gates/rtc-probe.sh --log FILE [--log FILE...]
#   ci/gates/rtc-probe.sh --live [--drift SEC]
#
# 背景：anaconda 在「update rtc with system clock」一步曾长时间停住，串口
# 上没有任何输出，现场无法判断是设备忙、等锁还是 ioctl 卡在硬件上。修法
# 不是把这一步去掉，而是让它可判定：限时、打点、超限只告警、慢有归因。
# 本门禁钉住这四条，三种视角各管一段。
#
#   ① 声明面（无参数）：限时器存在且被两个 kickstart 剖面按顺序嵌入，
#      打点标记齐全，实现里不出现 airy_fail 或非零 exit——它一旦能判负，
#      就会把一台能用的机器挡在装机之外；同时观察面的 rtc 主题在。
#   ② 取证面（--log）：解析装机日志里的打点块。BEGIN 之后必须有 END 和
#      结论；END 之后没有结论，或耗时超过限值却没有 SLOW 行，都判失败——
#      前者是打点断在半路，后者是告警本身失效，两者都比「慢」严重。
#      SLOW 与 SKIP 只记不判：它们是设计内的降级，不是缺陷。
#   ③ 实物面（--live）：读 airy-observe --section rtc。设备不在位判不适
#      用（5，不等于通过）；RTC 与系统时钟的偏差超过阈值只警告，因为首
#      启后 chronyd 会把系统时钟拉回，偏差本身不会让机器不可用，但超过
#      阈值说明装机那次写入没有落地。
#
# 退出码: 0 通过；1 判定失败；5 参数或环境不满足（含不适用）。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly MARK=AIRYMAX-RTC-

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
LOGS=()
LIVE=0
DRIFT=2
FAILS=0

usage()
{
    cat <<'EOF'
用法: ci/gates/rtc-probe.sh [--tree DIR]
      ci/gates/rtc-probe.sh --log FILE [--log FILE...]
      ci/gates/rtc-probe.sh --live [--drift SEC]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tree)
            [ -n "${2:-}" ] || { err "--tree 需要目录路径"; exit "${EINVAL}"; }
            [ -d "$2" ] || { err "--tree 目录不存在: $2"; exit "${EINVAL}"; }
            TREE="$2"
            shift 2
            ;;
        --log)
            [ -n "${2:-}" ] || { err "--log 需要文件路径"; exit "${EINVAL}"; }
            [ -r "$2" ] || { err "--log 文件不可读: $2"; exit "${EINVAL}"; }
            LOGS+=("$2")
            shift 2
            ;;
        --live) LIVE=1; shift ;;
        --drift)
            case "${2:-}" in
                ''|*[!0-9]*) err "--drift 需要非负整数秒"; exit "${EINVAL}" ;;
            esac
            DRIFT="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TREE}" ] || TREE="${ROOT}"
LIB="${TREE}/distro/kickstart/rtc-sync.sh"
OBS="${TREE}/distro/desktop/airy_observe.sh"
KSDIR="${TREE}/distro/kickstart"
LIMIT_MS=$(sed -n 's/.*AIRY_RTC_LIMIT:-\([0-9]*\).*/\1/p' "${LIB}" |
    head -n 1)
[ -n "${LIMIT_MS}" ] || LIMIT_MS=60
LIMIT_MS=$((LIMIT_MS * 1000))

bad() { err "$*"; FAILS=$((FAILS + 1)); }

# 声明面 -------------------------------------------------------------------

# 标记齐全：少任何一个，取证面都会把「没打点」误读成「没发生」。
chk_marks()
{
    local tag

    for tag in BEGIN END OK SLOW SKIP DRIFT TRACE; do
        grep -qF "${MARK}${tag}" "${LIB}" || bad "限时器缺少标记 ${MARK}${tag}"
    done
}

# 这一步不许判负：RTC 没写进去的后果是首启时间偏，不是装不上系统。
chk_nofail()
{
    local hits

    hits=$(grep -nE 'airy_fail|^[[:space:]]*exit[[:space:]]+[1-9]' "${LIB}")
    if [ -n "${hits}" ]; then
        printf '%s\n' "${hits}" >&2
        bad "限时器能判负（应只告警）"
    fi
    grep -q 'timeout "' "${LIB}" || bad "限时器未用 timeout 约束写入"
}

chk_lib()
{
    [ -r "${LIB}" ] || { bad "缺少 ${LIB#"${TREE}"/}"; return 0; }
    bash -n "${LIB}" || bad "限时器语法不通过"
    if grep -nE 'TODO|FIXME|XXX' "${LIB}"; then
        bad "限时器存在桩标记"
    fi
    grep -q 'AIRY_RTC_LIMIT:-60' "${LIB}" || bad "限时器默认限时不是 60 秒"
    chk_marks
    chk_nofail
}

# embed_before FILE E1 E2 —— 嵌入顺序是语义：限时器依赖 airy_out，必须排在
# install-lib.sh 之后，否则 source 时函数还不存在。
embed_before()
{
    local a b

    a=$(grep -nF -- "$1" "$2" | head -n 1 | cut -d: -f1)
    b=$(grep -nF -- "$3" "$2" | head -n 1 | cut -d: -f1)
    [ -n "${a}" ] && [ -n "${b}" ] && [ "${a}" -lt "${b}" ]
}

# 步数自洽：重编号时只要漏掉一行，装机日志里的阶段号就会与总步数不一致，
# 现场按步号判断进度就会错位。所有 airy_step 的总数必须只有一个取值。
chk_steps()
{
    local f n hits

    n=$(awk '$1 == "airy_step" { print $3 }' "$1" | sort -u | wc -l)
    [ "${n}" -eq 1 ] || bad "$(basename "$1") 的 airy_step 总数不唯一"
    hits=$(awk '$1 == "airy_step" && $4 == "rtc" && $2 != $3 { print $2"/"$3 }' \
        "$1")
    [ -z "${hits}" ] ||
        bad "$(basename "$1") 的 rtc 阶段号与总数不一致: ${hits}"
}

chk_ks()
{
    local f

    for f in "${KSDIR}"/*.ks; do
        grep -qF '# %airy-embed: rtc-sync.sh' "${f}" ||
            { bad "$(basename "${f}") 未嵌入 rtc-sync.sh"; continue; }
        embed_before '# %airy-embed: install-lib.sh' "${f}" \
            '# %airy-embed: rtc-sync.sh' ||
            bad "$(basename "${f}") 的 rtc-sync.sh 排在 install-lib.sh 之前"
        grep -qE '^airy_step [0-9]+ [0-9]+ rtc$' "${f}" ||
            bad "$(basename "${f}") 没有 rtc 阶段行"
        grep -qxF 'airy_rtc_sync' "${f}" ||
            bad "$(basename "${f}") 未调用 airy_rtc_sync"
        chk_steps "${f}"
    done
}

chk_obs()
{
    [ -r "${OBS}" ] || { bad "缺少 ${OBS#"${TREE}"/}"; return 0; }
    sh -n "${OBS}" || bad "观察面语法不通过"
    grep -qxF 'rtc_report() {' "${OBS}" || bad "观察面缺少 rtc_report"
    for k in rtc.device rtc.name rtc.epoch rtc.drift rtc.hctosys; do
        grep -qF "emit ${k} " "${OBS}" || bad "观察面缺少 ${k}"
    done
    grep -qE '^        rtc\) rtc_report ;;$' "${OBS}" ||
        bad "观察面未把 rtc 接入主题分发"
    grep -q '主题: .*rtc' "${OBS}" || bad "观察面用法未列出 rtc 主题"
}

chk_decl()
{
    say "声明面 ${TREE}"
    chk_lib
    chk_ks
    chk_obs
}

# 取证面 -------------------------------------------------------------------

# 每个 BEGIN 起一块，直到下一个 BEGIN 或文件结束。判断只看块本身，因此
# 同一份日志里出现多次装机（重试）时每块独立判定，不会互相掩盖。
blocks()
{
    awk -v deflim="${LIMIT_MS}" '
        function val(s, key,   a, n, i) {
            n = split(s, a, /[ \t]+/)
            for (i = 1; i <= n; i++)
                if (index(a[i], key "=") == 1)
                    return substr(a[i], length(key) + 2)
            return ""
        }
        function flush() {
            if (!blk) return
            if (!end) st = "incomplete"
            else if (con == "") st = "unjudged"
            else if (con == "SKIP") st = "skip"
            else if (con == "SLOW") st = "slow"
            else if (ms + 0 > deflim + 0) st = "silent"
            else st = "ok"
            printf "%d %s %s %s %s\n", blk, st,
                (ms == "" ? 0 : ms), (lim == "" ? deflim : lim),
                (rsn == "" ? "-" : rsn)
        }
        match($0, /AIRYMAX-RTC-(BEGIN|END|OK|SLOW|SKIP)/) {
            tag = substr($0, RSTART + 12, RLENGTH - 12)
            rest = substr($0, RSTART + RLENGTH)
            if (tag == "BEGIN") {
                flush()
                blk++
                end = 0; con = ""; ms = ""; lim = ""; rsn = ""
                next
            }
            if (!blk) next
            if (tag == "END") { end = 1; ms = val(rest, "ms"); next }
            con = tag
            ms = val(rest, "ms")
            lim = val(rest, "limit")
            rsn = val(rest, "reason")
        }
        END { flush(); if (!blk) print "0 none 0 " deflim " -" }
    ' "$1"
}

log_eval()
{
    local f idx st ms lim rsn n=0 ok=0 slow=0 skip=0

    for f in "${LOGS[@]}"; do
        while read -r idx st ms lim rsn; do
            [ "${st}" = none ] && { bad "${f}: 没有 RTC 打点"; continue; }
            n=$((n + 1))
            case "${st}" in
                ok) ok=$((ok + 1)) ;;
                slow)
                    slow=$((slow + 1))
                    note "${f}: 第 ${idx} 块写入超限 ms=${ms} limit=${lim} reason=${rsn}（告警成立，不判负）"
                    ;;
                skip)
                    skip=$((skip + 1))
                    note "${f}: 第 ${idx} 块跳过 reason=${rsn}"
                    ;;
                incomplete)
                    bad "${f}: 第 ${idx} 块有 BEGIN 无 END（停在这一步）"
                    ;;
                unjudged)
                    bad "${f}: 第 ${idx} 块有 END 无结论"
                    ;;
                silent)
                    bad "${f}: 第 ${idx} 块 ms=${ms} 超过 limit=${lim} 却没有 SLOW"
                    ;;
                *) bad "${f}: 第 ${idx} 块状态未知 ${st}" ;;
            esac
        done < <(blocks "${f}")
    done
    say "RTC 打点块 ${n}：ok=${ok} slow=${slow} skip=${skip}"
}

# 实物面 -------------------------------------------------------------------

field()
{
    sed -n "s/^AIRYMAX-OBSERVE $2=//p" <<<"$1" | head -n 1
}

is_num()
{
    case ${1:-} in
        ''|*[!0-9-]*) return 1 ;;
    esac
    case ${1#-} in
        ''|*[!0-9]*) return 1 ;;
    esac
}

live_probe()
{
    local out dev epoch drift hcto

    command -v airy-observe >/dev/null 2>&1 || {
        err "缺少命令: airy-observe"
        exit "${EINVAL}"
    }
    group "实物面"
    out=$(airy-observe --section rtc 2>/dev/null) || {
        err "airy-observe --section rtc 无法执行"
        exit "${EINVAL}"
    }
    dev=$(field "${out}" rtc.device)
    epoch=$(field "${out}" rtc.epoch)
    drift=$(field "${out}" rtc.drift)
    hcto=$(field "${out}" rtc.hctosys)
    if [ "${dev}" = absent ] || [ "${dev}" = none ]; then
        endgroup
        note "不适用：本机没有 RTC 设备（rtc.device=${dev}）"
        exit "${EINVAL}"
    fi
    say "RTC ${dev} epoch=${epoch:-absent} drift=${drift:-absent}s hctosys=${hcto:-absent}"
    is_num "${epoch}" || bad "RTC 读数为 ${epoch:-absent}（写入未落地）"
    if is_num "${drift}"; then
        if [ "${drift}" -gt "${DRIFT}" ] || [ "${drift}" -lt "-${DRIFT}" ]; then
            warn "RTC 与系统时钟相差 ${drift}s（阈值 ${DRIFT}s）：装机写入可能未落地"
        fi
    else
        bad "RTC 偏差为 ${drift:-absent}（since_epoch 不可读）"
    fi
    case "${hcto}" in
        0|1) ;;
        *) bad "hctosys=${hcto:-absent}（不是 0/1）" ;;
    esac
    endgroup
}

if [ "${#LOGS[@]}" -gt 0 ]; then
    group "取证面"
    log_eval
    endgroup
fi
if [ "${LIVE}" -eq 1 ]; then
    live_probe
fi
if [ "${#LOGS[@]}" -eq 0 ] && [ "${LIVE}" -eq 0 ]; then
    chk_decl
fi

if [ "${FAILS}" -ne 0 ]; then
    die "RTC 门禁失败 ${FAILS} 项"
fi

say "RTC 门禁通过"
exit 0
