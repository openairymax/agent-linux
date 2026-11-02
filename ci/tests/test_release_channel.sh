#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_channel.sh — ci/gates/release-channel.sh 的分支回归
#
# 门禁本身只做「找产物 + 调生成器」，所以这里既判参数面（缺件、多件、
# 未知开关），也判断言面（账本摘要被篡改后不得产出清单）。判据只看退出码、
# 文件是否存在与两个由产物决定的字段，不看措辞。
#
# 用法: bash ci/tests/test_release_channel.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/release-channel.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
OUT="${FX}/out"
ISO_DIR="${OUT}/desktop/iso"
RPM_DIR="${OUT}/rpms/RPMS/x86_64"
readonly VER=0.0.4
readonly BUILD=3

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${FX}/ci/gates/release-channel.sh" "$@" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

field()
{
    python3 -c 'import json, sys
print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${OUT}/channel.json" "$1"
}

mkfix()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" \
        "${FX}/tools/release-engineering" "${ISO_DIR}" "${RPM_DIR}"
    cp "${GATE}" "${FX}/ci/gates/release-channel.sh"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
    cp "${ROOT}/tools/release-engineering/release_manifest.py" \
        "${FX}/tools/release-engineering/release_manifest.py"
    printf 'iso-payload\n' >"${ISO_DIR}/airymaxos-${VER}.iso"
    ledger
    : >"${RPM_DIR}/airymaxos-kernel-${VER}-${BUILD}.x86_64.rpm"
    : >"${RPM_DIR}/airymaxos-release-${VER}-${BUILD}.noarch.rpm"
}

# 账本按 iso-publish.sh 的形态写：整体三元组 + 逐块摘要。
ledger()
{
    local iso="${ISO_DIR}/airymaxos-${VER}.iso"

    {
        printf 'ref=ghcr.io/openairymax/airymaxos-iso\n'
        printf 'tag=v%s\n' "${VER}"
        printf 'name=%s\n' "$(basename "${iso}")"
        printf 'sha256=%s\n' "$(sha256sum "${iso}" | cut -d' ' -f1)"
        printf 'size=%s\n' "$(stat -c %s "${iso}")"
        printf 'chunk_size=201326592\n'
        printf 'chunks=1\n'
        printf 'part=0 sha256=%s\n' "$(sha256sum "${iso}" | cut -d' ' -f1)"
    } >"${ISO_DIR}/iso-oci.txt"
}

mkfix

out="$(run --out "${OUT}" --version "${VER}")"
check "合规产物生成清单" 0 "$?" "${out}" "通道清单"
[ "$(field build)" = "${BUILD}" ] && ok "build 取自发行包 NEVRA" ||
    bad "build 应为 ${BUILD}，实得 $(field build)"
[ "$(field version)" = "${VER}" ] && ok "version 取自 --version" ||
    bad "version 应为 ${VER}"

out="$(run --out "${OUT}")"
check "缺 --version 退 5" 5 "$?" "${out}" "用法"

out="$(run --out "${tmp}/nope" --version "${VER}")"
check "产物根不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --out "${OUT}" --version "${VER}" --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

mkfix
rm -f "${ISO_DIR}/airymaxos-${VER}.iso"
out="$(run --out "${OUT}" --version "${VER}")"
check "缺 ISO 退 5" 5 "$?" "${out}" "ISO 缺失"

mkfix
cp "${ISO_DIR}/airymaxos-${VER}.iso" "${ISO_DIR}/airymaxos-other.iso"
out="$(run --out "${OUT}" --version "${VER}")"
check "两份 ISO 退 5" 5 "$?" "${out}" "应恰好一份"

mkfix
rm -f "${ISO_DIR}/iso-oci.txt"
out="$(run --out "${OUT}" --version "${VER}")"
check "缺账本退 5" 5 "$?" "${out}" "分块账本"

mkfix
rm -f "${RPM_DIR}/airymaxos-release-${VER}-${BUILD}.noarch.rpm"
out="$(run --out "${OUT}" --version "${VER}")"
check "缺发行包退 5" 5 "$?" "${out}" "发行 RPM 缺失"

mkfix
: >"${RPM_DIR}/airymaxos-kernel-${VER}-9.x86_64.rpm"
out="$(run --out "${OUT}" --version "${VER}")"
check "两份内核包退 5" 5 "$?" "${out}" "应恰好一份"

# 发行侧的内核资产是三个包（kernel / kernel-core / kernel-modules）。目录里
# 的三份包不等于三份内核：唯一性只能按主包名判，否则真实发布永远退 5。
mkfix
: >"${RPM_DIR}/airymaxos-kernel-core-${VER}-${BUILD}.x86_64.rpm"
: >"${RPM_DIR}/airymaxos-kernel-modules-${VER}-${BUILD}.x86_64.rpm"
out="$(run --out "${OUT}" --version "${VER}")"
check "core/modules 不计入内核唯一性" 0 "$?" "${out}" "通道清单"

# 跨 job 传递后发行包与内核包分属两个制品根，下载保留各自层级。门禁按
# 文件名递归取唯一一份，不要求二者同层，也不需要在 workflow 里搬文件。
mkfix
rm -rf "${tmp}/relch"
mkdir -p "${tmp}/relch/out/rpms/RPMS/x86_64" "${tmp}/relch/rel-rpms"
mv "${RPM_DIR}/airymaxos-release-${VER}-${BUILD}.noarch.rpm" \
    "${tmp}/relch/out/rpms/RPMS/x86_64/"
mv "${RPM_DIR}/airymaxos-kernel-${VER}-${BUILD}.x86_64.rpm" \
    "${tmp}/relch/rel-rpms/"
out="$(run --out "${OUT}" --version "${VER}" --rpm-dir "${tmp}/relch")"
check "跨层目录仍取唯一产物" 0 "$?" "${out}" "通道清单"

# 摘要被篡改：账本仍是旧值，ISO 字节已变。生成器必须判失败且不留清单。
mkfix
printf 'tampered-payload\n' >>"${ISO_DIR}/airymaxos-${VER}.iso"
out="$(run --out "${OUT}" --version "${VER}")"
check "ISO 摘要对不上账本退 1" 1 "$?" "${out}" "交叉断言失败"
[ -e "${OUT}/channel.json" ] && bad "断言失败仍产出清单" ||
    ok "断言失败不产出清单"

# 版本号与制品不一致：同一份产物被声明成另一个版本。
mkfix
out="$(run --out "${OUT}" --version 0.0.5)"
check "版本与制品不一致退 1" 1 "$?" "${out}" "交叉断言失败"

# notes_url 越域：客户端必然拒收，生成器应在产出前拦下。
mkfix
out="$(run --out "${OUT}" --version "${VER}" \
    --notes-url https://example.com/r/v${VER})"
check "notes_url 越域退 5" 5 "$?" "${out}" "发行域"

if [ "${fails}" -ne 0 ]; then
    printf 'test_release_channel: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_release_channel: 全部通过\n'
