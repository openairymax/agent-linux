#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_payload_seed.sh — ci/gates/payload-seed.sh 的分支回归
#
# 探针要对着实时上游取数，测试里用假 curl 顶替：它把请求的 URL 写进目标
# 文件，于是「上游变了」可以用一个盐模拟。断言四条：同输入同指纹、换架构
# 换指纹、上游变则指纹变、上游不可达时退 0 且不预热。取层侧复用假 oras，
# 判据是完成标记只在整层校验通过后出现。
#
# 用法: bash ci/tests/test_payload_seed.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/payload-seed.sh"
PUB="${ROOT}/ci/gates/payload-publish.sh"
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

mkdir -p "${tmp}/store"
# shellcheck source=lib/fake_oras.sh
. "${ROOT}/ci/tests/lib/fake_oras.sh"
fake_oras_install "${tmp}/bin"

cat >"${tmp}/bin/curl" <<'EOF'
#!/bin/sh
out=""
url=""
while [ $# -gt 0 ]; do
    case "$1" in
    -o) shift; out="$1"; shift ;;
    -*) shift ;;
    *) url="$1"; shift ;;
    esac
done
[ -n "${FAKE_CURL_FAIL:-}" ] && exit 22
printf '<repomd>%s%s</repomd>\n' "${FAKE_CURL_SALT:-}" "${url}" >"${out}"
exit 0
EOF
chmod 755 "${tmp}/bin/curl"

run()
{
    FAKE_STORE="${FAKE_STORE:-${tmp}/store}" FAKE_CURL_FAIL="${FAKE_CURL_FAIL:-}" \
        FAKE_CURL_SALT="${FAKE_CURL_SALT:-}" PATH="${tmp}/bin:${PATH}" \
        bash "${GATE}" "$@" 2>&1
}

tag_of()
{
    sed -n 's/^tag=//p' "$1" | head -1
}

run probe --arch x86_64 --output "${tmp}/o1" >/dev/null
t1="$(tag_of "${tmp}/o1")"
case "${t1}" in
seed-x86_64-*) ok "探针产出 seed-x86_64 tag" ;;
*) bad "探针 tag 形状不符: ${t1:-空}" ;;
esac

run probe --arch x86_64 --output "${tmp}/o2" >/dev/null
t2="$(tag_of "${tmp}/o2")"
[ "${t1}" = "${t2}" ] && ok "同输入同指纹" || bad "同输入指纹漂移（${t1} vs ${t2}）"

run probe --arch aarch64 --output "${tmp}/o3" >/dev/null
t3="$(tag_of "${tmp}/o3")"
[ "${t1}" != "${t3}" ] && ok "换架构换指纹" || bad "换架构指纹未变（${t3}）"

FAKE_CURL_SALT=v2 run probe --arch x86_64 --output "${tmp}/o4" >/dev/null
t4="$(tag_of "${tmp}/o4")"
[ "${t1}" != "${t4}" ] && ok "上游变则指纹变" || bad "上游变指纹未变（${t4}）"

out="$(FAKE_CURL_FAIL=1 run probe --arch x86_64 --output "${tmp}/o5")"
rc5=$?
t5="$(tag_of "${tmp}/o5")"
if [ "${rc5}" = "0" ] && [ -z "${t5}" ]; then
    ok "上游不可达退 0 且不预热"
else
    bad "上游不可达处理不符（tag=${t5:-空}）"
    printf '%s\n' "${out}" >&2
fi

mkdir -p "${tmp}/pkg"
printf 'a' >"${tmp}/pkg/a.rpm"
printf 'bb' >"${tmp}/pkg/b.rpm"
sum_a="$(sha256sum "${tmp}/pkg/a.rpm" | cut -d' ' -f1)"
sum_b="$(sha256sum "${tmp}/pkg/b.rpm" | cut -d' ' -f1)"
{
    printf '# AirymaxOS payload manifest\narch=x86_64\ncount=2\n'
    printf 'rpm %s a.rpm\n' "${sum_a}"
    printf 'rpm %s b.rpm\n' "${sum_b}"
} >"${tmp}/man.txt"

PATH="${tmp}/bin:${PATH}" FAKE_STORE="${tmp}/store" bash "${PUB}" push \
    --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" --tag "${t1}" \
    --ref "ghcr.io/x/y" >/dev/null 2>&1

out="$(run fetch --dir "${tmp}/pool" --arch x86_64 --ref "ghcr.io/x/y")"
if [ -f "${tmp}/pool/.seed-complete" ]; then
    ok "整层校验通过后写完成标记"
else
    bad "未写完成标记"
    printf '%s\n' "${out}" >&2
fi

out="$(run fetch --dir "${tmp}/pool" --arch x86_64 --ref "ghcr.io/x/y")"
case "${out}" in
*已完整*) ok "已有完成标记则跳过取层" ;;
*) bad "未跳过取层"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run fetch --dir "${tmp}/pool2" --arch riscv64 --ref "ghcr.io/x/y")"
if [ "$?" = "0" ] && [ ! -f "${tmp}/pool2/.seed-complete" ]; then
    ok "层不存在时不写标记且退 0"
else
    bad "层不存在时行为不符"
    printf '%s\n' "${out}" >&2
fi

# 预热只推已有字节，判据是池的输入标签与实时指纹一致：标签对得上才推，
# 上游在两次之间变过就把旧字节挂在旧 key 上，取层构建会整层丢弃。
out="$(run warm --dir "${tmp}/pkg" --arch x86_64 --ref "ghcr.io/x/y")"
rc=$?
case "${out}" in
*无输入标签*) ok "池无输入标签时不预热" ;;
*) bad "无标签时未跳过（rc=${rc}）"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run warm --dir "${tmp}/pool" --arch x86_64 --ref "ghcr.io/x/y")"
rc=$?
if [ "${rc}" = "0" ] && case "${out}" in *跳过推送*) true ;; *) false ;; esac; then
    ok "标签一致时按当前指纹预热"
else
    bad "标签一致时未预热（rc=${rc}）"
    printf '%s\n' "${out}" >&2
fi

mkdir -p "${tmp}/stale"
cp -p "${tmp}/pool/a.rpm" "${tmp}/stale/"
printf 'tag=seed-x86_64-0000000000000000\nkey=0\n' >"${tmp}/stale/.input-key"
out="$(run warm --dir "${tmp}/stale" --arch x86_64 --ref "ghcr.io/x/y")"
case "${out}" in
*"本轮不预热"*) ok "标签陈旧时不预热" ;;
*) bad "标签陈旧时仍然推送"; printf '%s\n' "${out}" >&2 ;;
esac

mkdir -p "${tmp}/empty"
cp -p "${tmp}/pool/.input-key" "${tmp}/empty/"
out="$(run warm --dir "${tmp}/empty" --arch x86_64 --ref "ghcr.io/x/y")"
case "${out}" in
*"池内没有 RPM"*) ok "空池不预热" ;;
*) bad "空池仍然推送"; printf '%s\n' "${out}" >&2 ;;
esac

missing_of()
{
    sed -n 's/^missing=//p' "$1" | head -1
}

# 层存在时不应该再起预热（整池恢复约 2.5 GB，上游一天只变几批）。x86_64 的
# 层在前面的用例里已经推过，riscv64 的没有。
run probe --arch x86_64 --check --ref "ghcr.io/x/y" --output "${tmp}/c1" >/dev/null
[ "$(missing_of "${tmp}/c1")" = "0" ] &&
    ok "层已存在时 probe 报告无需预热" ||
    bad "层已存在时 missing 不为 0（$(missing_of "${tmp}/c1")）"

run probe --arch riscv64 --check --ref "ghcr.io/x/y" --output "${tmp}/c2" >/dev/null
[ "$(missing_of "${tmp}/c2")" = "1" ] &&
    ok "层缺失时 probe 报告需要预热" ||
    bad "层缺失时 missing 不为 1（$(missing_of "${tmp}/c2")）"

exit "${fails}"
