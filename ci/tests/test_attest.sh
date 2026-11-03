#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_attest.sh — ci/gates/attest.sh 的分支回归
#
# 证明是「镜像之外的那一份记录」，所以测试的重点不在 JSON 长得对不对，而在
# 三处判断：同一输入必须产出同一份字节（幂等的前提）、buildId 必须取自镜像
# 自带清单（不能自圆其说）、ISO 与发行账本对不上时必须拒绝出证。发布侧复用
# 假 oras，判据是推后回读一致、同份跳过、异份须 --force。
#
# 用法: bash ci/tests/test_attest.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/attest.sh"
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

mkdir -p "${tmp}/store" "${tmp}/image/desktop/iso"
# shellcheck source=lib/fake_oras.sh
. "${ROOT}/ci/tests/lib/fake_oras.sh"
fake_oras_install "${tmp}/bin"

iso="${tmp}/image/desktop/iso/AirymaxOS-test.iso"
printf 'iso-bytes\n' >"${iso}"
( cd "${iso%/*}" && sha256sum "${iso##*/}" >"${iso##*/}.sha256" )

run()
{
    FAKE_STORE="${tmp}/store" PATH="${tmp}/bin:${PATH}" \
        AIRYMAX_BUILD_ID="${AIRYMAX_BUILD_ID:-test-build-1}" \
        bash "${GATE}" "$@" 2>&1
}

file="${tmp}/image/desktop/iso/attest-v0.0.4.json"

out="$(run build --out "${tmp}/image" --tag v0.0.4)"
if [ -f "${file}" ]; then
    ok "build 产出证明文件"
else
    bad "build 未产出证明"
    printf '%s\n' "${out}" >&2
fi

grep -q '"predicateType": "https://slsa.dev/provenance/v1"' "${file}" &&
    ok "谓词类型为 SLSA v1" ||
    bad "谓词类型不符"

grep -q '"buildId": "test-build-1"' "${file}" &&
    ok "buildId 取自构建指纹" ||
    bad "buildId 未按指纹写入"

sum1="$(sha256sum "${file}" | cut -d' ' -f1)"
out="$(run build --out "${tmp}/image" --tag v0.0.4)"
sum2="$(sha256sum "${file}" | cut -d' ' -f1)"
[ "${sum1}" = "${sum2}" ] &&
    ok "同输入重跑字节不变" ||
    bad "同输入重跑字节漂移（${sum1} vs ${sum2}）"

{
    printf 'build_id=from-manifest-7\n'
} >"${tmp}/image/image-ci-manifest.txt"
out="$(run build --out "${tmp}/image" --tag v0.0.4)"
grep -q '"buildId": "from-manifest-7"' "${file}" &&
    ok "清单存在时以清单的 buildId 为准" ||
    bad "清单未覆盖 buildId"

out="$(run verify --file "${file}" --iso "${iso}" --build-id from-manifest-7)"
case "${out}" in
*AIRYMAX-ATTEST-PASS*) ok "verify 在摘要与指纹一致时通过" ;;
*) bad "verify 未通过"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run verify --file "${file}" --iso "${iso}" --build-id other-build)"
case "${out}" in
*"buildId 不符"*) ok "verify 在指纹不符时失败" ;;
*) bad "verify 未拦住指纹不符"; printf '%s\n' "${out}" >&2 ;;
esac

cp -p "${iso}" "${tmp}/other.iso"
printf 'x' >>"${tmp}/other.iso"
out="$(run verify --file "${file}" --iso "${tmp}/other.iso")"
case "${out}" in
*"ISO 摘要与证明不符"*) ok "verify 在 ISO 被改过时失败" ;;
*) bad "verify 未拦住摘要不符"; printf '%s\n' "${out}" >&2 ;;
esac

# 出证前必须与发行账本对账：账本摘要仍是旧值，说明 ISO 在账本之后被改过。
printf 'iso-bytes\nmore\n' >"${iso}"
out="$(run build --out "${tmp}/image" --tag v0.0.4)"
case "${out}" in
*"发行账本摘要与证明不符"*) ok "ISO 与账本不符时拒绝出证" ;;
*) bad "未拦住账本不符"; printf '%s\n' "${out}" >&2 ;;
esac
printf 'iso-bytes\n' >"${iso}"
( cd "${iso%/*}" && sha256sum "${iso##*/}" >"${iso##*/}.sha256" )
out="$(run build --out "${tmp}/image" --tag v0.0.4)"

out="$(run publish --out "${tmp}/image" --tag v0.0.4 --ref ghcr.io/x/y)"
if [ -f "${tmp}/store/.meta/ghcr.io_x_y_attest-v0.0.4.json" ]; then
    ok "publish 推入 attest-<tag> 并回读一致"
else
    bad "publish 未推送"
    printf '%s\n' "${out}" >&2
fi

out="$(run publish --out "${tmp}/image" --tag v0.0.4 --ref ghcr.io/x/y)"
case "${out}" in
*"同一份证明已发布"*) ok "同份重推跳过" ;;
*) bad "同份重推未跳过"; printf '%s\n' "${out}" >&2 ;;
esac

printf ' ' >>"${file}"
out="$(run publish --out "${tmp}/image" --tag v0.0.4 --ref ghcr.io/x/y)"
case "${out}" in
*"已存在不同证明"*) ok "异份未加 --force 时拒绝" ;;
*) bad "异份未被拒绝"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run publish --out "${tmp}/image" --tag v0.0.4 --ref ghcr.io/x/y --force)"
case "${out}" in
*"回读一致"*) ok "异份加 --force 后可替换" ;;
*) bad "--force 替换失败"; printf '%s\n' "${out}" >&2 ;;
esac

exit "${fails}"
