#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_verify.sh — ci/gates/release-verify.sh 的分支回归
#
# 门禁有两种模式：物料自证（--dir）与发行自证（--iso/--iso-dir）。前者已在
# release.yml 上跑了一版，后者守的是「ISO 里的内核 == 本版 %release 的发行
# 包」这条 0.0.3 曾断裂的边。真实证据只在构建机与 Release 上存在，判定逻辑
# 只能由合成夹具驱动：这里造一份 spec、一组内核 RPM 与一份载荷清单，再逐条
# 破坏单一前提，断言只看退出码与稳定判据名。
#
# 真实 ISO 是否合规不在这里判：image-build.yml 的 release-verify 步负责那
# 一条，本文件只保证门禁没有漏判与误判。
#
# 用法: bash ci/tests/test_release_verify.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/release-verify.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
REL="${tmp}/rel"
PAY="${tmp}/pay/payload-manifest.txt"
ISO="${tmp}/iso/airymaxos-0.0.4-x86_64.iso"
MAT="${tmp}/mat"

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/specs/kernel"
cp "${GATE}" "${FX}/ci/gates/release-verify.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

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
    bash "${FX}/ci/gates/release-verify.sh" "$@" 2>&1
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

sum_of()
{
    sha256sum "$1" | cut -d' ' -f1
}

# spec <version> <release> — 最小 spec，只有发行自证会读的 %global。
spec()
{
    cat >"${FX}/distro/specs/kernel/airymaxos-kernel.spec" <<EOF
%global airy_version $1
%global airy_release $2
%global kernel_version 6.6.157

Name:           airymaxos-kernel
Version:        %{airy_version}
Release:        %{airy_release}%{?dist}
EOF
}

# rpm <文件名> — 内容随名字变化，摘要才有区分度。
rpm()
{
    printf 'rpm payload %s\n' "$1" >"${REL}/$1"
}

# mkfix <version-release> — 造一套自洽的发行目录、载荷清单与 ISO。
mkfix()
{
    local nv="$1" n sum
    local -a names=(
        "airymaxos-kernel-${nv}.x86_64.rpm"
        "airymaxos-kernel-core-${nv}.x86_64.rpm"
        "airymaxos-kernel-modules-${nv}.x86_64.rpm"
        "airymaxos-release-${nv}.x86_64.rpm"
    )

    rm -rf "${REL}" "${tmp}/pay" "${tmp}/iso"
    mkdir -p "${REL}" "${tmp}/pay" "${tmp}/iso"
    for n in "${names[@]}"; do
        rpm "${n}"
    done
    {
        printf '# AirymaxOS payload manifest\n'
        printf 'arch=x86_64\n'
        printf 'count=%s\n' "${#names[@]}"
        for n in "${names[@]}"; do
            sum="$(sum_of "${REL}/${n}")"
            printf 'rpm %s %s\n' "${sum}" "${n}"
        done
    } >"${PAY}"
    printf 'iso bytes %s\n' "${nv}" >"${ISO}"
    ( cd "${tmp}/iso" && sha256sum "${ISO##*/}" >"${ISO##*/}.sha256" )
}

# pay_drop <模式> — 从载荷清单里删掉匹配的行。
pay_drop()
{
    grep -v "$1" "${PAY}" >"${tmp}/pay/keep" || true
    mv "${tmp}/pay/keep" "${PAY}"
}

# mat <小节数> — 物料自证夹具：摘要清单、署名、notes（指定 ## 小节数）。
mat()
{
    local sections="$1" name i=1

    rm -rf "${MAT}"
    mkdir -p "${MAT}"
    for name in airymaxos-kernel.x86_64.rpm airymaxos-kernel.x86_64.rpm.asc sbom.json airymaxos-sdk.tar.gz; do
        printf 'material %s\n' "${name}" >"${MAT}/${name}"
    done
    # 公钥与分离署名同为 .asc，门禁只能按首行内容区分，夹具必须两种都有。
    {
        printf -- '-----BEGIN PGP PUBLIC KEY BLOCK-----\n'
        printf 'key bytes of the release key\n'
        printf -- '-----END PGP PUBLIC KEY BLOCK-----\n'
    } >"${MAT}/agent-linux-1.2.0-gpg-key.asc"
    ( cd "${MAT}" && sha256sum airymaxos-kernel.x86_64.rpm \
        airymaxos-kernel.x86_64.rpm.asc sbom.json airymaxos-sdk.tar.gz \
        agent-linux-1.2.0-gpg-key.asc \
        >agent-linux-1.2.0.sha256sum )
    printf 'signature of checksums\n' >"${MAT}/agent-linux-1.2.0.sha256sum.asc"
    {
        printf '## 概述\n- 版本号 v0.0.4\n'
        while [ "${i}" -lt "${sections}" ]; do
            printf '## 小节%s\n- 说明%s\n' "${i}" "${i}"
            i=$(( i + 1 ))
        done
    } >"${MAT}/agent-linux-1.2.0-release-notes.md"
    printf 'signature of notes\n' >"${MAT}/agent-linux-1.2.0-release-notes.md.asc"
}

spec 0.0.4 2
mkfix 0.0.4-2

out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "合规发行自证通过" 0 "$?" "${out}" "发行自证通过"
case "${out}" in
*"iso  $(sum_of "${ISO}")"*) ok "对照表含 ISO 摘要" ;;
*) bad "对照表含 ISO 摘要"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run --iso-dir "${tmp}/iso" --payload "${PAY}" --kernel-rpms "${REL}")"
check "目录发现 ISO 通过" 0 "$?" "${out}" "发行自证通过"

out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}" \
    --spec "${FX}/distro/specs/kernel/airymaxos-kernel.spec")"
check "显式 spec 通过" 0 "$?" "${out}" "发行自证通过"

mkfix 0.0.4-2.oe2403
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "%dist 后缀 release 仍判本版" 0 "$?" "${out}" "发行自证通过"

mkfix 0.0.4-2
printf 'tampered\n' >>"${ISO}"
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "ISO 摘要不符判失败" 1 "$?" "${out}" "ISO 摘要不符"

mkfix 0.0.4-2
( cd "${tmp}/iso" && printf '%s  other.iso\n' "$(sum_of "${ISO}")" >"${ISO##*/}.sha256" )
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "ISO 摘要文件名不符判失败" 1 "$?" "${out}" "ISO 摘要指向"

mkfix 0.0.4-2
rm -f "${ISO}.sha256"
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "缺 ISO 摘要退 5" 5 "$?" "${out}" "缺少 ISO 摘要"

mkfix 0.0.4-2
sed 's/^rpm [0-9a-f]* \(airymaxos-kernel-0\.0\.4-2\)/rpm 0000 \1/' "${PAY}" >"${tmp}/pay/x"
mv "${tmp}/pay/x" "${PAY}"
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "载荷内核摘要不符判失败" 1 "$?" "${out}" "内核包摘要不符"

mkfix 0.0.4-2
rm -f "${REL}/airymaxos-kernel-core-0.0.4-2.x86_64.rpm"
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "发行目录缺内核包判失败" 1 "$?" "${out}" "发行目录缺内核包"

mkfix 0.0.4-2
pay_drop 'kernel-modules'
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "载荷缺模块包判失败" 1 "$?" "${out}" "载荷缺内核 modules 包"

mkfix 0.0.4-2
pay_drop 'airymaxos-kernel-0\.0\.4-2'
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "载荷缺主包判失败" 1 "$?" "${out}" "载荷缺内核主包"

mkfix 0.0.5-2
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "NEVRA 版本不符判失败" 1 "$?" "${out}" "airy_version"

mkfix 0.0.4-5
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "NEVRA release 不符判失败" 1 "$?" "${out}" "airy_release"

mkfix 0.0.4-2
out="$(run --payload "${PAY}" --kernel-rpms "${REL}")"
check "缺 ISO 退 5" 5 "$?" "${out}" "缺少 --iso"

out="$(run --iso "${ISO}" --payload "${PAY}")"
check "缺发行目录退 5" 5 "$?" "${out}" "--kernel-rpms 必填"

out="$(run --iso "${ISO}" --iso-dir "${tmp}/iso" --payload "${PAY}" --kernel-rpms "${REL}")"
check "--iso 与 --iso-dir 同给退 5" 5 "$?" "${out}" "只能取一个"

out="$(run --dir "${MAT}" --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "--dir 与发行自证同用退 5" 5 "$?" "${out}" "不可同用"

mkdir -p "${tmp}/nodir"
out="$(run --iso-dir "${tmp}/nodir" --payload "${PAY}" --kernel-rpms "${REL}")"
check "目录内无 ISO 退 5" 5 "$?" "${out}" "没有 ISO"

mkdir -p "${tmp}/two"
printf 'a\n' >"${tmp}/two/a.iso"
printf 'b\n' >"${tmp}/two/b.iso"
out="$(run --iso-dir "${tmp}/two" --payload "${PAY}" --kernel-rpms "${REL}")"
check "目录内 ISO 不唯一退 5" 5 "$?" "${out}" "不唯一"

out="$(run --iso "${tmp}/nope.iso" --payload "${PAY}" --kernel-rpms "${REL}")"
check "ISO 不存在退 5" 5 "$?" "${out}" "ISO 不存在"

out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${tmp}/nope")"
check "发行目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --iso "${ISO}" --payload "${tmp}/nope.txt" --kernel-rpms "${REL}")"
check "载荷清单不存在退 5" 5 "$?" "${out}" "载荷清单不存在"

mv "${FX}/distro/specs/kernel/airymaxos-kernel.spec" "${tmp}/spec.bak"
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "缺 spec 退 5" 5 "$?" "${out}" "缺少内核 spec"
mv "${tmp}/spec.bak" "${FX}/distro/specs/kernel/airymaxos-kernel.spec"

printf '%%global airy_version 0.0.4\n' >"${FX}/distro/specs/kernel/airymaxos-kernel.spec"
out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}")"
check "spec 缺 airy_release 退 5" 5 "$?" "${out}" "airy_release"
spec 0.0.4 2

out="$(run --iso "${ISO}" --payload "${PAY}" --kernel-rpms "${REL}" --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run --iso)"
check "参数缺值退 5" 5 "$?" "${out}" "需要参数值"

mat 3
out="$(run --dir "${MAT}")"
check "合规物料自证通过" 0 "$?" "${out}" "物料自证通过"
case "${out}" in
*"1 份公钥"*) ok "公钥计入物料自证" ;;
*) bad "公钥计入物料自证"; printf '%s\n' "${out}" >&2 ;;
esac

# 公钥与署名同后缀，门禁按首行内容区分：把公钥当署名会要求一份并不存在
# 的被签文件，发行页上有公钥反而判负。
mat 3
: >"${MAT}/agent-linux-1.2.0-gpg-key.asc"
( cd "${MAT}" && sha256sum airymaxos-kernel.x86_64.rpm \
    airymaxos-kernel.x86_64.rpm.asc sbom.json airymaxos-sdk.tar.gz \
    agent-linux-1.2.0-gpg-key.asc >agent-linux-1.2.0.sha256sum )
out="$(run --dir "${MAT}")"
check "空公钥判失败" 1 "$?" "${out}" "空署名或空公钥"

# 反向：真正的分离署名缺了被签文件，仍必须判负，不能因为放宽公钥而漏判。
mat 3
printf 'signature without payload\n' >"${MAT}/lonely.rpm.asc"
( cd "${MAT}" && sha256sum airymaxos-kernel.x86_64.rpm \
    airymaxos-kernel.x86_64.rpm.asc sbom.json airymaxos-sdk.tar.gz \
    agent-linux-1.2.0-gpg-key.asc lonely.rpm.asc >agent-linux-1.2.0.sha256sum )
out="$(run --dir "${MAT}")"
check "署名缺被签文件仍判失败" 1 "$?" "${out}" "署名缺少被签文件"

mat 2
out="$(run --dir "${MAT}")"
check "notes 小节数不足判失败" 1 "$?" "${out}" "应为 3"

mat 3
rm -f "${MAT}/agent-linux-1.2.0.sha256sum.asc"
out="$(run --dir "${MAT}")"
check "缺摘要清单署名判失败" 1 "$?" "${out}" "缺少署名"

mat 3
printf 'tampered\n' >>"${MAT}/sbom.json"
out="$(run --dir "${MAT}")"
check "物料摘要不符判失败" 1 "$?" "${out}" "摘要不符"

if [ "${fails}" -ne 0 ]; then
    printf 'test_release_verify: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_release_verify: 全部通过\n'
