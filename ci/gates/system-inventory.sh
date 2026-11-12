#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/system-inventory.sh — 装机后系统完整性判定的门禁
#
# 用法:
#   ci/gates/system-inventory.sh
#
# 0.0.4 判据①要求装机后三个面同时干净：没有 failed 单元、本次开机没有
# err 日志、实际包集合与装机写下的交付集合之差为 0。这三个面各有独立
# 证据源，只查其中一面会放过另外两种真实形态——单元失败而日志停在 info，
# 日志刷错而没有单元失败，包被悄悄换成旧版本。
#
# 本门禁在构建机上做两件事，缺一不可：
#
#   声明面：包装脚本确实转交 system、模块的交付清单路径与装机 %post 写入
#   的路径一致、两份 profile 都写了这份清单、冒烟脚本确实取 airy-observe
#   system 的 state 作为 T23 判定。这五项此前只靠装机后才发现断链。
#
#   行为面：用四份固定样本驱动真实模块，核对 0/1/3 三个退出码与四个
#   state token。判定只认观测，所以门禁自己也只认退出码与 state，不读
#   模块内部的中间量。
#
# 退出码: 0 通过；1 检查失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

OBS="${ROOT}/system/services"
SYS="${OBS}/airymax_observe/sys.py"
WRAP="${ROOT}/distro/desktop/airy_observe.sh"
SMOKE="${ROOT}/distro/ci/run-smoke.sh"
KS_MIN="${ROOT}/distro/kickstart/airymaxos-minimal.ks"
KS_WS="${ROOT}/distro/kickstart/airymaxos-workstation.ks"
MANIFEST="/etc/airymaxos-packages"

TMP=""

usage()
{
    cat <<'EOF'
用法: ci/gates/system-inventory.sh
EOF
}

cleanup()
{
    [ -n "${TMP}" ] && rm -rf "${TMP}"
    return 0
}

trap cleanup EXIT

# 声明面 -------------------------------------------------------------------

check_wrap()
{
    [ -f "${WRAP}" ] || die "缺少包装脚本: ${WRAP#"${ROOT}/"}"
    grep -qF '[ "$1" = system ]' "${WRAP}" ||
        die "包装脚本未转交 system 子命令"
    grep -qF 'exec python3 -B -m airymax_observe "$@"' "${WRAP}" ||
        die "包装脚本未把参数交给 airymax_observe"
    say "包装脚本转交 system 子命令"
}

check_path()
{
    [ -f "${SYS}" ] || die "缺少清点模块: ${SYS#"${ROOT}/"}"
    grep -qxF "MANIFEST = \"${MANIFEST}\"" "${SYS}" ||
        die "清点模块的交付清单路径不是 ${MANIFEST}"
    say "交付清单路径与装机写入一致"
}

check_install()
{
    local ks
    for ks in "${KS_MIN}" "${KS_WS}"; do
        [ -f "${ks}" ] || die "缺少配方: ${ks#"${ROOT}/"}"
        grep -qF "${MANIFEST}" "${ks}" ||
            die "$(basename "${ks}") 未写交付包集合"
        grep -qF "rpm -qa --qf '%{NAME}\\n'" "${ks}" ||
            die "$(basename "${ks}") 未从 rpm 数据库取包名"
    done
    say "两份 profile 都落盘交付包集合"
}

check_smoke()
{
    local line
    [ -f "${SMOKE}" ] || die "缺少冒烟脚本: ${SMOKE#"${ROOT}/"}"
    line="$(grep -n '^probe 23 ' "${SMOKE}")" ||
        die "冒烟脚本未登记 T23"
    grep -qF 'airy-observe system --manifest' "${SMOKE}" ||
        die "T23 未取 airy-observe system 的判定"
    [ -n "${line}" ] || die "T23 探针为空"
    say "T23 取同一份判定实现"
}

# 行为面 -------------------------------------------------------------------

inventory() # inventory <units> <journal> <rpm> <manifest>
{
    PYTHONPATH="${OBS}" python3 -B -m airymax_observe system \
        --units "$1" --journal "$2" --rpm "$3" --manifest "$4"
}

expect()
{
    local got="$1" want="$2" what="$3"

    [ "${got}" -eq "${want}" ] ||
        die "${what}: 退出码 ${got}，应为 ${want}"
}

expect_state()
{
    local out="$1" want="$2" what="$3"

    case " ${out} " in
        *" state=${want} "*) ;;
        *) die "${what}: 未给出 state=${want}: ${out}" ;;
    esac
}

fixtures()
{
    printf 'a\nb\n' >"${TMP}/rpm"
    printf 'a\nb\n' >"${TMP}/packs"
    : >"${TMP}/units"
    : >"${TMP}/journal"
}

check_clean()
{
    local out rc

    out="$(inventory "${TMP}/units" "${TMP}/journal" "${TMP}/rpm" \
        "${TMP}/packs")"
    rc=$?
    expect "${rc}" 0 "三面干净"
    expect_state "${out}" ok "三面干净"
    say "三面干净判 0"
}

unit_failed()
{
    local out rc

    printf 'airymax-desktop.service loaded failed failed\n' \
        >"${TMP}/units"
    out="$(inventory "${TMP}/units" "${TMP}/journal" "${TMP}/rpm" \
        "${TMP}/packs")"
    rc=$?
    expect "${rc}" 1 "单元失败"
    expect_state "${out}" failed "单元失败"
    : >"${TMP}/units"
    say "单元失败判 1"
}

check_log_error()
{
    local out rc

    printf '[  2.1] amuser.service: Failed to start\n' >"${TMP}/journal"
    out="$(inventory "${TMP}/units" "${TMP}/journal" "${TMP}/rpm" \
        "${TMP}/packs")"
    rc=$?
    expect "${rc}" 1 "err 日志"
    expect_state "${out}" failed "err 日志"
    : >"${TMP}/journal"
    say "err 日志判 1"
}

pack_drift()
{
    local out rc

    printf 'a\nb\nc\n' >"${TMP}/rpm"
    out="$(inventory "${TMP}/units" "${TMP}/journal" "${TMP}/rpm" \
        "${TMP}/packs")"
    rc=$?
    expect "${rc}" 1 "包差集"
    expect_state "${out}" drift "包差集"
    printf 'a\nb\n' >"${TMP}/rpm"
    say "包差集判 1"
}

check_unknown()
{
    local out rc

    out="$(inventory "${TMP}/units" "${TMP}/journal" "${TMP}/rpm" \
        "${TMP}/absent")"
    rc=$?
    expect "${rc}" 3 "交付清单缺失"
    expect_state "${out}" unknown "交付清单缺失"
    say "证据缺失判 3"
}

main()
{
    TMP="$(mktemp -d)" || die "无法创建临时目录"
    fixtures

    group "系统完整性: 声明面"
    check_wrap
    check_path
    check_install
    check_smoke
    endgroup

    group "系统完整性: 行为面"
    check_clean
    unit_failed
    check_log_error
    pack_drift
    check_unknown
    endgroup

    say "== system-inventory passed =="
}

case "${1:-}" in
    '' ) main ;;
    -h|--help) usage ;;
    *) err "未知参数: $1"; usage >&2; exit 1 ;;
esac
