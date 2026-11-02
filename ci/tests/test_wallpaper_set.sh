#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_wallpaper_set.sh — ci/gates/wallpaper-set.sh 的分支回归
#
# 门禁判的是「台账 × 实物」的双向闭合，真实树常年为绿，因此判定逻辑必须
# 用负例驱动：静态面逐条破坏台账、实物、spec、注册表、schema 默认值与上游
# 排除；安装树面逐条破坏目录集合、digest、注册项与默认值。断言只看退出码
# 与稳定判据名，不看措辞。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh wallpaper-set 负责那一条，
# 本文件只保证门禁没有漏判与误判。
#
# 用法: bash ci/tests/test_wallpaper_set.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/wallpaper-set.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/lockfile" \
    "${FX}/distro/release/wallpapers" "${FX}/distro/specs" \
    "${FX}/distro/desktop/wallpaper" \
    "${FX}/distro/desktop/glib-2.0/schemas" \
    "${FX}/distro/comps" "${FX}/distro/kickstart"
cp "${GATE}" "${FX}/ci/gates/wallpaper-set.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

readonly A=alpha_photo.jpg
readonly B=beta_photo.jpg
readonly C=gamma_photo.jpg

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
    bash "${FX}/ci/gates/wallpaper-set.sh" "$@" 2>&1
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

sum()
{
    sha256sum "$1" | cut -d' ' -f1
}

papers()
{
    local d="${FX}/distro/release/wallpapers"
    rm -f "${d}"/*.jpg
    printf 'kite\n' >"${d}/${A}"
    printf 'lake\n' >"${d}/${B}"
    printf 'snow\n' >"${d}/${C}"
}

lock()
{
    local d="${FX}/distro/release/wallpapers" n
    {
        printf 'packages:\n'
        for n in "${A}" "${B}" "${C}"; do
            printf '  - name: %s\n' "${n}"
            printf '    sha256: %s\n' "$(sum "${d}/${n}")"
        done
        printf '\nupstream:\n  - ukui-wallpapers\n  - mate-backgrounds\n'
    } >"${FX}/distro/lockfile/ukui-wallpapers.yaml"
}

spec()
{
    local n
    {
        printf '%%check\n'
        for n in "${A}" "${B}" "${C}"; do
            printf 'Source5: %s\n' "${n}"
            printf '%%{_datadir}/backgrounds/airymaxos/%s\n' "${n}"
        done
    } >"${FX}/distro/specs/airymaxos-release.spec"
}

reg()
{
    local n
    {
        printf '<?xml version="1.0" encoding="UTF-8"?>\n'
        printf '<!DOCTYPE wallpapers SYSTEM "gnome-wp-list.dtd">\n'
        printf '<wallpapers>\n'
        for n in "${A}" "${B}" "${C}"; do
            printf '<wallpaper deleted="false">\n'
            printf '<name>AirymaxOS</name>\n'
            printf '<filename>%s/%s</filename>\n' \
                "/usr/share/backgrounds/airymaxos" "${n}"
            printf '</wallpaper>\n'
        done
        printf '</wallpapers>\n'
    } >"${FX}/distro/desktop/wallpaper/airymaxos-default.xml"
}

sch()
{
    local out="${FX}/distro/desktop/glib-2.0/schemas"
    {
        printf '[org.mate.background]\n'
        printf "picture-filename='%s/%s'\n" \
            "/usr/share/backgrounds/airymaxos" "${A}"
    } >"${out}/99-airymaxos-default.gschema.override"
}

rpms()
{
    printf 'packages:\n  - NetworkManager\n' \
        >"${FX}/distro/lockfile/rpms.in.yaml"
    printf '<comps/>\n' >"${FX}/distro/comps/airymaxos-comps.xml"
    printf '%%packages\n@airymax-workstation\n%%end\n' \
        >"${FX}/distro/kickstart/airymaxos-workstation.ks"
}

mkfix()
{
    papers
    lock
    spec
    reg
    sch
    rpms
}

ROOTDIR="${tmp}/root"
mkroot()
{
    local n
    rm -rf "${ROOTDIR}"
    mkdir -p "${ROOTDIR}/usr/share/backgrounds/airymaxos" \
        "${ROOTDIR}/usr/share/ukui-background-properties" \
        "${ROOTDIR}/usr/share/glib-2.0/schemas"
    for n in "${A}" "${B}" "${C}"; do
        cp "${FX}/distro/release/wallpapers/${n}" \
            "${ROOTDIR}/usr/share/backgrounds/airymaxos/${n}"
    done
    cp "${FX}/distro/desktop/wallpaper/airymaxos-default.xml" \
        "${ROOTDIR}/usr/share/ukui-background-properties/"
    cp "${FX}/distro/desktop/glib-2.0/schemas/"*.override \
        "${ROOTDIR}/usr/share/glib-2.0/schemas/"
}

mkfix
mkroot

out="$(run --source "${FX}")"
check "源树合规判定通过" 0 "$?" "${out}" "壁纸集合通过"

out="$(run)"
check "无参数退 5" 5 "$?" "${out}" "至少给出"

out="$(run --source "${tmp}/nope")"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

mkfix
sed -i "/  - name: ${C}/,+1d" \
    "${FX}/distro/lockfile/ukui-wallpapers.yaml"
out="$(run --source "${FX}")"
check "台账只有两张判拒绝" 1 "$?" "${out}" "lockfile: 集合应恰为"

mkfix
rm -f "${FX}/distro/release/wallpapers/${C}"
out="$(run --source "${FX}")"
check "实物少一张判拒绝" 1 "$?" "${out}" "papers/set"

mkfix
printf 'hail\n' >"${FX}/distro/release/wallpapers/${A}"
out="$(run --source "${FX}")"
check "实物 digest 与台账不符判拒绝" 1 "$?" "${out}" \
    "papers/sha256"

mkfix
sed -i "/^Source5: ${B}$/d" \
    "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --source "${FX}")"
check "spec 缺 Source 条目判拒绝" 1 "$?" "${out}" "spec/source"

mkfix
sed -i "\|^%{_datadir}/backgrounds/airymaxos/${C}$|d" \
    "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --source "${FX}")"
check "spec 缺 %files 落点判拒绝" 1 "$?" "${out}" "spec/files"

mkfix
sed -i "/${C}<\/filename>/d" \
    "${FX}/distro/desktop/wallpaper/airymaxos-default.xml"
out="$(run --source "${FX}")"
check "注册表少一条判拒绝" 1 "$?" "${out}" "registry/count"

mkfix
sed -i "s/${A}/unknown.jpg/" \
    "${FX}/distro/desktop/wallpaper/airymaxos-default.xml"
out="$(run --source "${FX}")"
check "注册表指向台账外文件判拒绝" 1 "$?" "${out}" "registry/entry"

mkfix
sed -i "s/${A}/unknown.jpg/" \
    "${FX}/distro/desktop/glib-2.0/schemas/"*.override
out="$(run --source "${FX}")"
check "schema 默认值不在台账内判拒绝" 1 "$?" "${out}" \
    "schema/default"

mkfix
printf '# airymaxos-default.jpg\n' \
    >>"${FX}/distro/specs/airymaxos-release.spec"
out="$(run --source "${FX}")"
check "旧别名复活判拒绝" 1 "$?" "${out}" "papers/legacy"

mkfix
printf 'packages:\n  - ukui-wallpapers\n' \
    >"${FX}/distro/lockfile/rpms.in.yaml"
out="$(run --source "${FX}")"
check "上游壁纸包进闭包判拒绝" 1 "$?" "${out}" "upstream/closure"

mkfix
out="$(run --root "${ROOTDIR}")"
check "安装树合规判定通过" 0 "$?" "${out}" "壁纸集合通过"

mkroot
mkdir "${ROOTDIR}/usr/share/backgrounds/openEuler"
out="$(run --root "${ROOTDIR}")"
check "安装树出现第二个壁纸目录判拒绝" 1 "$?" "${out}" \
    "root/backgrounds"

mkroot
cp "${FX}/distro/release/wallpapers/${A}" \
    "${ROOTDIR}/usr/share/backgrounds/airymaxos/extra.jpg"
out="$(run --root "${ROOTDIR}")"
check "安装树多一张壁纸判拒绝" 1 "$?" "${out}" "root/set"

mkroot
printf 'hail\n' \
    >"${ROOTDIR}/usr/share/backgrounds/airymaxos/${A}"
out="$(run --root "${ROOTDIR}")"
check "安装树 digest 与台账不符判拒绝" 1 "$?" "${out}" \
    "root/sha256"

mkroot
sed -i "/${C}<\/filename>/d" \
    "${ROOTDIR}/usr/share/ukui-background-properties/airymaxos-default.xml"
out="$(run --root "${ROOTDIR}")"
check "安装树注册表少一条判拒绝" 1 "$?" "${out}" \
    "root/registry-count"

mkroot
rm -f "${ROOTDIR}/usr/share/backgrounds/airymaxos/${C}"
out="$(run --root "${ROOTDIR}")"
check "安装树注册项指向缺失文件判拒绝" 1 "$?" "${out}" \
    "root/registry-file"

mkroot
sed -i "s/${A}/unknown.jpg/" \
    "${ROOTDIR}/usr/share/glib-2.0/schemas/"*.override
out="$(run --root "${ROOTDIR}")"
check "安装树 schema 默认值越界判拒绝" 1 "$?" "${out}" \
    "root/schema"

if [ "${fails}" -ne 0 ]; then
    printf 'test_wallpaper_set: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_wallpaper_set: 全部通过\n'
