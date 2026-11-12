#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/model-manifest.sh — 模型资产通道门禁
#
# 用法:
#   ci/gates/model-manifest.sh
#
# 两段判定，缺一不可：
#
#   声明面：入口可执行、单元与 preset 指向同一条启动命令、配方确实把
#   airy-model 与 airyllm 打进包里。这四项此前只在装机后才暴露——0.0.3
#   的镜像里 preset 钉着 airy-llm.service，配方却没有交付该单元，启用
#   时只得到「unit not found」。
#
#   实物面：用临时离线包跑一遍 install → status → verify，按退出码与
#   状态字段核对 0/10/1 契约与 degraded:<原因> token。资产通道的语义
#   全在退出码上，只看日志无法判断调用方该继续还是该停。
#
# 仓库内不得出现 *.gguf：权重是外部交付物，进源码树既过不了评审，也会
# 让镜像体积随模型数量线性增长。此处按工作树判定，包含未跟踪文件。
#
# 退出码: 0 通过；1 检查失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

AIRY="${ROOT}/system/bin/airy-model"
UNIT="${ROOT}/system/units/airy-llm.service"
PRESET="${ROOT}/distro/presets/90-airymax.preset"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"

TMP=""
HOME_DIR=""

usage()
{
    cat <<'EOF'
用法: ci/gates/model-manifest.sh
EOF
}

# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

trap cleanup EXIT

# 声明面 -------------------------------------------------------------------

check_entry()
{
    chk_entry "${AIRY}"
    say "入口可执行且带 shebang"
}

check_unit()
{
    local want
    [ -f "${UNIT}" ] || die "缺少单元文件: ${UNIT#"${ROOT}/"}"
    for want in 'ExecStart=/usr/bin/airy-model serve' \
                'User=airy' \
                'StateDirectory=airy-llm' \
                'PartOf=agentrt.target' \
                'ConditionPathExists=/var/lib/airy-llm/manifest.json'; do
        grep -qxF "${want}" "${UNIT}" ||
            die "单元缺少契约行: ${want}"
    done
    grep -qxF 'disable airy-llm.service' "${PRESET}" ||
        die "preset 未把 airy-llm.service 钉为 disabled"
    say "单元启动面与 preset 一致"
}

check_package()
{
    local want
    [ -f "${SPEC}" ] || die "缺少配方: ${SPEC#"${ROOT}/"}"
    for want in 'source-bundle ../../system/services/airyllm' \
                'airyllm-%{version}.tar.gz' \
                'airy-llm.service' \
                '%{_bindir}/airy-model' \
                '%{python3_sitelib}/airyllm'; do
        grep -qF "${want}" "${SPEC}" ||
            die "配方未交付: ${want}"
    done
    say "配方交付入口、单元与运行包"
}

no_weights()
{
    local found
    found="$(find "${ROOT}" -name '*.gguf' -not -path '*/.git/*' \
        -print -quit)"
    [ -z "${found}" ] || die "源码树内出现模型权重: ${found#"${ROOT}/"}"
    say "源码树内无模型权重"
}

# 实物面 -------------------------------------------------------------------

# shellcheck source=../lib/model-assets.sh
. "${ROOT}/ci/lib/model-assets.sh"

# 期望的退出码与原因；rc 0 表示就绪，10 表示降级，1 表示失败。
expect()
{
    local got="$1" want="$2" what="$3"

    [ "${got}" -eq "${want}" ] ||
        die "${what}: 退出码 ${got}，应为 ${want}"
}

fields()
{
    python3 - "$@" <<'PY'
import json
import sys
raw, want_state, want_reason, want_digest = sys.argv[1:5]
obj = json.loads(raw)
keys = sorted(obj)
if keys != ["backend", "digest", "reason", "state"]:
    sys.exit(f"字段集合 {keys!r} 偏离契约")
got = (obj["state"], obj["reason"])
want = (want_state, want_reason or None)
if got != want:
    sys.exit(f"state/reason {got!r}，应为 {want!r}")
if want_digest and obj["digest"] != want_digest:
    sys.exit(f"清单摘要 {obj['digest']!r}，应为 {want_digest!r}")
PY
}

check_channel()
{
    local out rc digest

    make_bundle
    HOME_DIR="${TMP}/home"
    out="$("${AIRY}" --root "${HOME_DIR}" install --source "${TMP}/bundle")"
    rc=$?
    expect "${rc}" 0 "install"
    cmp -s "${TMP}/bundle/models/w.gguf" "${HOME_DIR}/models/w.gguf" ||
        die "install 未落盘资产内容"

    out="$("${AIRY}" --root "${HOME_DIR}" install --source "${TMP}/bundle")"
    rc=$?
    expect "${rc}" 0 "install 重跑"
    case "${out}" in
        *'0 file(s) written') ;;
        *) die "install 重跑未幂等: ${out}" ;;
    esac

    out="$("${AIRY}" --root "${HOME_DIR}" status)"
    rc=$?
    expect "${rc}" 10 "status"
    case "${out}" in
        *state=degraded*reason=no-accel*) ;;
        *) die "status 未给出 degraded:no-accel: ${out}" ;;
    esac

    digest="$(sha256sum "${HOME_DIR}/manifest.json" | cut -d' ' -f1)"
    out="$("${AIRY}" --root "${HOME_DIR}" verify --json)"
    rc=$?
    expect "${rc}" 10 "verify"
    fields "${out}" degraded no-accel "${digest}" ||
        die "verify 字段不符: ${out}"
    say "install/status/verify 按 0/10 契约收敛"
}

check_tamper()
{
    local weight="${HOME_DIR}/models/w.gguf"
    local size out rc

    [ -f "${weight}" ] || die "缺少已落盘资产: ${weight}"
    size="$(wc -c <"${weight}")"
    head -c "${size}" /dev/zero >"${weight}"
    out="$("${AIRY}" --root "${HOME_DIR}" verify --json)"
    rc=$?
    expect "${rc}" 1 "verify 篡改"
    fields "${out}" failed digest-mismatch "" ||
        die "verify 未归因到 digest-mismatch: ${out}"
    [ -f "${HOME_DIR}/status.json" ] || die "失败判定未落盘"
    say "篡改被识别为 digest-mismatch 且失败落盘"
}

check_empty()
{
    local out rc

    mkdir -p "${TMP}/empty"
    out="$("${AIRY}" --root "${TMP}/empty" status)"
    rc=$?
    expect "${rc}" 1 "空根 status"
    case "${out}" in
        *state=failed*reason=no-manifest*) ;;
        *) die "空根未归因到 no-manifest: ${out}" ;;
    esac
    [ -f "${TMP}/empty/status.json" ] || die "空根失败判定未落盘"
    say "空根按 no-manifest 失败并落盘"
}

main()
{
    TMP="$(mktemp -d)" || die "无法创建临时目录"

    group "模型资产通道: 声明面"
    check_entry
    check_unit
    check_package
    no_weights
    endgroup

    group "模型资产通道: 实物面"
    check_channel
    check_tamper
    check_empty
    endgroup

    say "== model-manifest passed =="
}

case "${1:-}" in
    '' ) main ;;
    -h|--help) usage ;;
    *) err "未知参数: $1"; usage >&2; exit 1 ;;
esac
