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

# 改字节而不改名：这是「同一份物被替换」，判据必须是摘要。
printf 'x' >>"${iso}"
out="$(run verify --file "${file}" --iso "${iso}")"
case "${out}" in
*"ISO 摘要与证明不符"*) ok "verify 在 ISO 被改过时失败" ;;
*) bad "verify 未拦住摘要不符"; printf '%s\n' "${out}" >&2 ;;
esac

# 只换文件名、字节不变：判据必须落在名字上，否则「拿另一份同内容的东西
# 来对证明」也会通过。
cp -p "${iso}" "${tmp}/other.iso"
out="$(run verify --file "${file}" --iso "${tmp}/other.iso")"
case "${out}" in
*"ISO 集合与证明不符"*) ok "verify 在 ISO 换了名字时失败" ;;
*) bad "verify 未拦住名字不符"; printf '%s\n' "${out}" >&2 ;;
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

# ---- 发布资产（--kind rel）-----------------------------------------------
# 资产集不另立清单：它由 agent-linux-<ver>.sha256sum 推导，因此夹具必须像
# 真实发布目录——清单登记了谁，谁就在场；oci-image 只经 ghcr 交付，即使
# 登记在清单里也不该进集合。
rel="${tmp}/rel"
rpm1="airymaxos-kernel-0.0.4-1.x86_64.rpm"
rpm2="airymaxos-base-0.0.4-1.x86_64.rpm"
mkdir -p "${rel}"
printf 'kernel-rpm\n' >"${rel}/${rpm1}"
printf 'base-rpm\n' >"${rel}/${rpm2}"
printf 'oci-layer\n' >"${rel}/oci-image-v0.0.4.tar"
printf 'signed\n' >"${rel}/agent-linux-0.0.4.sha256sum.asc"
printf '# notes\n' >"${rel}/agent-linux-0.0.4-release-notes.md"
printf 'signed\n' >"${rel}/agent-linux-0.0.4-release-notes.md.asc"
( cd "${rel}" && sha256sum "${rpm1}" "${rpm2}" oci-image-v0.0.4.tar \
    >agent-linux-0.0.4.sha256sum )

rfile="${rel}/attest-v0.0.4.json"
out="$(run build --kind rel --out "${rel}" --tag v0.0.4)"
if [ -f "${rfile}" ]; then
    ok "rel build 在发布目录产出证明"
else
    bad "rel build 未产出证明"
    printf '%s\n' "${out}" >&2
fi

grep -q 'release-assets/v1' "${rfile}" &&
    ok "rel 证明的 buildType 指向发布资产" ||
    bad "rel buildType 不符"

# 4 份清单周边（清单、清单署名、notes、notes 署名）+ 2 个 RPM。
grep -q '"subjectCount": 6' "${rfile}" &&
    ok "rel 集合为 6 项" ||
    bad "rel 集合计数不符"

grep -q 'oci-image' "${rfile}" &&
    bad "oci-image 被算进了发布资产集" ||
    ok "oci-image 不入发布资产集"

out="$(run verify --file "${rfile}" --dir "${rel}")"
case "${out}" in
*AIRYMAX-ATTEST-PASS*) ok "verify 在整套资产一致时通过" ;;
*) bad "rel verify 未通过"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run publish --kind rel --out "${rel}" --tag v0.0.4 --ref ghcr.io/x/rel)"
if grep -q '"org.openairymax.attest.subjects":"6"' \
    "${tmp}/store/.meta/ghcr.io_x_rel_attest-v0.0.4.json" 2>/dev/null; then
    ok "rel publish 注解记录 subject 条数"
else
    bad "rel publish 缺 subjects 注解"
    printf '%s\n' "${out}" >&2
fi

printf 'tampered\n' >>"${rel}/${rpm2}"
out="$(run verify --file "${rfile}" --dir "${rel}")"
case "${out}" in
*"资产 摘要与证明不符"*) ok "verify 在资产被改过时失败" ;;
*) bad "rel verify 未拦住摘要不符"; printf '%s\n' "${out}" >&2 ;;
esac
printf 'base-rpm\n' >"${rel}/${rpm2}"

mv "${rel}/${rpm2}" "${tmp}/moved.rpm"
out="$(run verify --file "${rfile}" --dir "${rel}")"
case "${out}" in
*"发布资产缺失"*) ok "verify 在资产缺件时失败" ;;
*) bad "rel verify 未拦住缺件"; printf '%s\n' "${out}" >&2 ;;
esac
mv "${tmp}/moved.rpm" "${rel}/${rpm2}"

# 证明自身被删掉一条 subject：集合比对必须双向，反向漏检等于默认「少一项
# 也算一致」。
python3 - "${rfile}" "${tmp}/short.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    st = json.load(fh)
st["subject"] = st["subject"][:-1]
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump(st, fh, sort_keys=True, indent=2)
PY
out="$(run verify --file "${tmp}/short.json" --dir "${rel}")"
case "${out}" in
*"资产 集合与证明不符"*) ok "verify 在证明少一项时失败" ;;
*) bad "rel verify 未拦住集合缩水"; printf '%s\n' "${out}" >&2 ;;
esac

exit "${fails}"
