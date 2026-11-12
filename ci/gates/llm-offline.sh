#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/llm-offline.sh — 离线可用性与首启自检门禁
#
# 用法: ci/gates/llm-offline.sh
#
# 本门禁回答运维在装完机后问的第一个问题：断网时，一条命令能否判定本机
# 模型栈到底能不能用，并把不能用的原因归到具体一层。判定逻辑由单测逐项
# 覆盖，此处钉「对外表现」——退出码、状态字段、逐项结论与被阻塞项。
#
# 声明面：
#
#   * 入口可执行，帮助里能看到 selftest 子命令。
#   * 自检包以单一包形态交付，不与旧的单文件并存。
#   * 自检包不引入网络客户端——「完全断网可用」不能靠运气。
#   * 单元在清单缺失时不启动：没装模型的机器不该反复拉起必败服务，
#     更不该每次开机都往事故单元里记一笔。
#
# 实物面（造一份临时离线包，与 model-manifest 同一套手法）：
#
#   * 就绪或显式降级：8 项全过，退出码 0 或 10；降级必须写
#     degraded:no-accel，判定同时落盘。
#   * 引擎失去可执行位：1 / failed:no-runtime，失败项之前的项保持通过，
#     之后的项逐条记 blocked，而不是从输出里消失。
#   * 资产被等长篡改：1 / failed:digest-mismatch，归因落在 assets。
#   * 空运行根：1 / failed:no-manifest。
#   * 行式输出逐项同形，末行与 status 一样是状态行。
#
# 加速探测结果随机器而变，故就绪与降级不作硬断言：两种都接受，但降级
# 必须给出原因。除这条以外，其余判定与真机显卡无关。
#
# 退出码: 0 通过；1 检查失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

AIRY="${ROOT}/system/bin/airy-model"
PKG="${ROOT}/system/services/airyllm/selftest"
UNIT="${ROOT}/system/units/airy-llm.service"
MANIFEST_GATE=/var/lib/airy-llm/manifest.json

NAMES="root manifest assets engine smoke backend serve status"

TMP=""
HOME_DIR=""

usage()
{
    cat <<'EOF'
用法: ci/gates/llm-offline.sh
EOF
}

# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

trap cleanup EXIT

# 声明面 -------------------------------------------------------------------

check_entry()
{
    chk_entry "${AIRY}"
    "${AIRY}" --help | grep -q 'selftest' ||
        die "命令行帮助里没有 selftest 子命令"
    say "入口可执行且暴露 selftest"
}

check_layout()
{
    local part
    [ -f "${PKG}/__init__.py" ] || die "缺少自检包: ${PKG#"${ROOT}/"}"
    for part in verdict.py steps.py; do
        [ -f "${PKG}/${part}" ] || die "自检包缺少 ${part}"
    done
    [ ! -e "${ROOT}/system/services/airyllm/selftest.py" ] ||
        die "单文件 selftest.py 与自检包并存"
    say "自检实现以单一包形态交付"
}

check_no_net()
{
    local hit
    hit="$(grep -nE '^[[:space:]]*(import|from)[[:space:]]+(socket|ssl|urllib|http|ftplib|smtplib|telnetlib|xmlrpc|requests)' \
        "${PKG}"/*.py || true)"
    [ -z "${hit}" ] || die "自检包引入网络依赖: ${hit}"
    say "自检包不依赖网络客户端"
}

check_unit()
{
    [ -f "${UNIT}" ] || die "缺少单元文件: ${UNIT#"${ROOT}/"}"
    grep -qxF "ConditionPathExists=${MANIFEST_GATE}" "${UNIT}" ||
        die "单元未在清单缺失时跳过启动"
    say "单元在清单缺失时跳过启动"
}

# 实物面 -------------------------------------------------------------------

# shellcheck source=../lib/model-assets.sh
. "${ROOT}/ci/lib/model-assets.sh"

install_bundle()
{
    local out rc

    out="$("${AIRY}" --root "${HOME_DIR}" install --source "${TMP}/bundle")"
    rc=$?
    [ "${rc}" -eq 0 ] || die "install 退出码 ${rc}: ${out}"
}

# 自检判定与契约的比对。失败时要求：首个失败项就是归因项，其后每一项都
# 记成被它阻塞并沿用同一归因，最后一项状态检查必须通过——判定没落盘就
# 等于没判定。三份契约（JSON、行式、状态文件）的实现只在
# ci/lib/llm_contract.py 一处，这里只负责把参数递过去。
verdict()
{
    python3 "${ROOT}/ci/lib/llm_contract.py" json "${NAMES}" "$@"
}

# 行式输出是给人看的那一面：逐项一段，末行与 status 完全同形。
lines_contract()
{
    python3 "${ROOT}/ci/lib/llm_contract.py" lines "${NAMES}" "$1"
}

status_file()
{
    python3 "${ROOT}/ci/lib/llm_contract.py" status "$1" "$2"
}

check_ready()
{
    local out rc digest

    make_bundle
    install_bundle
    out="$("${AIRY}" --root "${HOME_DIR}" selftest --json)"
    rc=$?
    digest="$(sha256sum "${HOME_DIR}/manifest.json" | cut -d' ' -f1)"
    case "${rc}" in
        0)  verdict "${out}" ready "" "" "${digest}" ||
                die "就绪判定不符: ${out}" ;;
        10) verdict "${out}" degraded no-accel "" "${digest}" ||
                die "降级判定不符: ${out}" ;;
        *)  die "自检退出码 ${rc}: ${out}" ;;
    esac
    status_file "${HOME_DIR}" "${out}" || die "判定未按契约落盘"
    say "离线自检收敛到就绪或显式降级"
}

check_lines()
{
    local out rc

    out="$("${AIRY}" --root "${HOME_DIR}" selftest)"
    rc=$?
    case "${rc}" in
        0|10) ;;
        *) die "行式自检退出码 ${rc}: ${out}" ;;
    esac
    lines_contract "${out}" || die "行式输出偏离契约: ${out}"
    say "行式输出逐项同形且以状态行收尾"
}

check_engine()
{
    local engine="${TMP}/engine/llama-server" out rc

    chmod 0644 "${engine}"
    out="$("${AIRY}" --root "${HOME_DIR}" selftest --json)"
    rc=$?
    chmod 0755 "${engine}"
    [ "${rc}" -eq 1 ] || die "引擎不可执行时退出码 ${rc}，应为 1"
    verdict "${out}" failed no-runtime engine ||
        die "未归因到 no-runtime: ${out}"
    say "引擎不可执行按 no-runtime 失败并阻断后续项"
}

check_tamper()
{
    local weight="${HOME_DIR}/models/w.gguf" size out rc

    [ -f "${weight}" ] || die "缺少已落盘资产: ${weight}"
    size="$(wc -c <"${weight}")"
    head -c "${size}" /dev/zero >"${weight}"
    out="$("${AIRY}" --root "${HOME_DIR}" selftest --json)"
    rc=$?
    [ "${rc}" -eq 1 ] || die "篡改后退出码 ${rc}，应为 1"
    verdict "${out}" failed digest-mismatch assets ||
        die "未归因到 digest-mismatch: ${out}"
    say "等长篡改按 digest-mismatch 失败"
}

check_empty()
{
    local out rc

    mkdir -p "${TMP}/empty"
    out="$("${AIRY}" --root "${TMP}/empty" selftest --json)"
    rc=$?
    [ "${rc}" -eq 1 ] || die "空根退出码 ${rc}，应为 1"
    verdict "${out}" failed no-manifest manifest ||
        die "空根未归因到 no-manifest: ${out}"
    say "空根按 no-manifest 失败"
}

main()
{
    TMP="$(mktemp -d)" || die "无法创建临时目录"
    HOME_DIR="${TMP}/home"

    group "离线自检: 声明面"
    check_entry
    check_layout
    check_no_net
    check_unit
    endgroup

    group "离线自检: 实物面"
    check_ready
    check_lines
    check_engine
    check_tamper
    check_empty
    endgroup

    say "== llm-offline passed =="
}

case "${1:-}" in
    '' ) main ;;
    -h|--help) usage ;;
    *) err "未知参数: $1"; usage >&2; exit 1 ;;
esac
