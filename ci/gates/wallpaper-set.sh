#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/wallpaper-set.sh — 内置壁纸集合门禁（R1-15 / §16.4.10）
#
# 用法:
#   ci/gates/wallpaper-set.sh [--source DIR] [--root DIR]
#
# 背景：0.0.3 的镜像里只有一张改名后的默认图，选择器看不到第二张；上游
# ukui-wallpapers / mate-backgrounds 一旦进闭包又会带进整目录壁纸，集合
# 随上游滚动。0.0.4 收敛为三张品牌默认集，文件名与 digest 由
# lockfile/ukui-wallpapers.yaml 唯一定义，本门禁按「台账 × 实物」双断言：
# 多一件说明上游壁纸漏进镜像，少一件说明注册表指向了不存在的文件。
#
# 两种模式可同时给：
#   --source DIR  仓库静态面：台账三条、实物集合与 digest、spec 的 Source
#                 与 %files 落点、注册表文件名、schema 默认值、旧别名与
#                 上游包不得复活；
#   --root DIR    已安装树：/usr/share/backgrounds 只允许 airymaxos/，其下
#                 *.jpg 与台账同集合、同 digest，已装注册表每条 filename
#                 都落在台账内且文件存在，schema 默认值也在台账内。
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

# 安装后的壁纸目录；注册表与 schema 默认值都指向这里。
readonly PAPERS=/usr/share/backgrounds/airymaxos
readonly COUNT=3
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/opts.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/opts.sh"

SOURCE=""
ROOT=""
FAILS=0
NAMES=()
SHAS=()
UPN=()

usage()
{
    cat <<'EOF'
用法: ci/gates/wallpaper-set.sh [--source DIR] [--root DIR]
EOF
}

opts_tree "$@"

bad() { err "$*"; FAILS=$((FAILS + 1)); }

# load_lock FILE —— 解析台账。packages 段每条两行（name + sha256），条数
# 必须恰为 COUNT；upstream 段是拒绝清单。解析失败不短路，后续判据仍会
# 各自报出缺失项。
load_lock()
{
    local f="$1" line sect=""

    NAMES=()
    SHAS=()
    UPN=()
    [ -f "${f}" ] || { bad "lockfile: 缺少 ${f}"; return 0; }
    while IFS= read -r line; do
        case "${line}" in
            packages:*) sect=packages ;;
            upstream:*) sect=upstream ;;
            "  - name: "*)
                [ "${sect}" = packages ] &&
                    NAMES+=("${line#  - name: }")
                ;;
            "    sha256: "*)
                [ "${sect}" = packages ] &&
                    SHAS+=("${line#    sha256: }")
                ;;
            "  - "*)
                [ "${sect}" = upstream ] && UPN+=("${line#  - }")
                ;;
        esac
    done <"${f}"
    if [ "${#NAMES[@]}" -ne "${COUNT}" ] ||
        [ "${#SHAS[@]}" -ne "${COUNT}" ]; then
        bad "lockfile: 集合应恰为 ${COUNT} 条" \
            "（name=${#NAMES[@]} sha=${#SHAS[@]}）"
    fi
}

# has_name NAME —— 名称是否在台账内；注册表与默认值共用这一处成员判定。
has_name()
{
    local n
    for n in "${NAMES[@]}"; do
        [ "${n}" = "$1" ] && return 0
    done
    return 1
}

# chk_up DIR —— 上游壁纸包不得进入安装闭包：它们各自携带整目录壁纸，
# 一旦进闭包，镜像里就会出现台账外的第四件，事后删除又违反 §16.4.10。
chk_up()
{
    local d="$1" p
    for p in "${UPN[@]}"; do
        if grep -rqF -- "${p}" "${d}/distro/lockfile/rpms.in.yaml" \
            "${d}/distro/comps" "${d}/distro/kickstart" 2>/dev/null; then
            bad "upstream/closure: ${p} 进入安装闭包"
        fi
    done
}

chk_src()
{
    local d="$1"
    local w="${d}/distro/release/wallpapers"
    local spec="${d}/distro/specs/airymaxos-release.spec"
    local reg="${d}/distro/desktop/wallpaper/airymaxos-default.xml"
    local sch="${d}/distro/desktop/glib-2.0/schemas"
    local have want f n s i

    say "静态集合 ${d}"
    load_lock "${d}/distro/lockfile/ukui-wallpapers.yaml"
    [ "${#NAMES[@]}" -eq "${COUNT}" ] || return 0

    # 1) 实物集合按 basename 排序逐字比对；集合不一致时仍逐条报缺失项，
    #    便于一次看清差在哪一张。
    have=$(find "${w}" -maxdepth 1 -type f -name '*.jpg' \
        -printf '%f\n' 2>/dev/null | sort)
    want=$(printf '%s\n' "${NAMES[@]}" | sort)
    [ "${have}" = "${want}" ] ||
        bad "papers/set: 实物与台账不一致（${have//$'\n'/ }）"

    # 2) digest：同名不同内容说明有一侧被单独替换过，只比文件名不够。
    for i in "${!NAMES[@]}"; do
        n="${NAMES[$i]}"
        f="${w}/${n}"
        [ -f "${f}" ] || { bad "papers/file: 缺少 ${f}"; continue; }
        s=$(sha256sum "${f}" | cut -d' ' -f1)
        [ "${s}" = "${SHAS[$i]}" ] ||
            bad "papers/sha256: ${n} 与台账摘要不符"
    done

    # 3) spec：每张壁纸都要有 Source 与 %files 落点。%files 缺项会让
    #    rpmbuild 直接失败，但它只能证明「装了某个文件」，与 Source 成对
    #    才说明装的是台账里那一个。
    for n in "${NAMES[@]}"; do
        grep -qE "^Source[0-9]+:[[:space:]]+${n}$" "${spec}" ||
            bad "spec/source: ${n} 无 Source 条目"
        grep -qxF "%{_datadir}/backgrounds/airymaxos/${n}" "${spec}" ||
            bad "spec/files: ${n} 未进 %files"
    done

    # 4) 注册表与 schema 默认值只能指向台账内的文件。
    have=$(grep -cE '<filename>[^<]+</filename>' "${reg}" 2>/dev/null)
    [ "${have:-0}" -eq "${COUNT}" ] ||
        bad "registry/count: 应恰为 ${COUNT} 条（实为 ${have:-0}）"
    while IFS= read -r n; do
        has_name "${n##*/}" || bad "registry/entry: ${n##*/} 不在台账内"
    done < <(sed -n 's:.*<filename>\([^<]*\)</filename>.*:\1:p' \
        "${reg}" 2>/dev/null)

    n=$(sed -n "s/^picture-filename='\([^']*\)'$/\1/p" \
        "${sch}"/*.override 2>/dev/null | head -n 1)
    [ -n "${n}" ] || bad "schema/default: 无 picture-filename 覆盖"
    [ -z "${n}" ] || has_name "${n##*/}" ||
        bad "schema/default: ${n##*/} 不在台账内"

    # 5) 旧别名不得复活：它只有一张图，会把默认壁纸变回改名后的产物。
    #    changelog 与 README 的历史叙述不算残留，故只查真正决定安装结果的
    #    配方（%changelog 之前）、注册表与 schema 覆盖。
    if sed '/^%changelog$/,$d' "${spec}" 2>/dev/null |
        grep -qF 'airymaxos-default.jpg'; then
        bad "papers/legacy: 配方仍引用旧别名 airymaxos-default.jpg"
    fi
    if grep -rqF 'airymaxos-default.jpg' "${d}/distro/desktop/wallpaper" \
        "${d}/distro/desktop/glib-2.0" 2>/dev/null; then
        bad "papers/legacy: 注册表或 schema 仍引用旧别名"
    fi

    chk_up "${d}"
}

chk_root()
{
    local r="$1" top
    local bg="${r}${PAPERS}"
    local reg="${r}/usr/share/ukui-background-properties"
    local sch="${r}/usr/share/glib-2.0/schemas"
    local have want f n s i

    say "已安装树 ${r}"
    top=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
    load_lock "${top}/distro/lockfile/ukui-wallpapers.yaml"
    [ "${#NAMES[@]}" -eq "${COUNT}" ] || return 0

    # 1) 上层目录只允许 airymaxos/：上游包若进闭包，整目录会落在这里。
    have=$(find "${r}/usr/share/backgrounds" -mindepth 1 -maxdepth 1 \
        -printf '%f\n' 2>/dev/null | sort)
    [ "${have}" = "airymaxos" ] ||
        bad "root/backgrounds: 仅允许 airymaxos/（实为 ${have//$'\n'/ }）"

    # 2) 集合与 digest：镜像里的壁纸必须与台账同集合、同内容。
    have=$(find "${bg}" -maxdepth 1 -type f -name '*.jpg' \
        -printf '%f\n' 2>/dev/null | sort)
    want=$(printf '%s\n' "${NAMES[@]}" | sort)
    [ "${have}" = "${want}" ] ||
        bad "root/set: 镜像集合与台账不一致（${have//$'\n'/ }）"
    for i in "${!NAMES[@]}"; do
        n="${NAMES[$i]}"
        f="${bg}/${n}"
        [ -f "${f}" ] || continue
        s=$(sha256sum "${f}" | cut -d' ' -f1)
        [ "${s}" = "${SHAS[$i]}" ] ||
            bad "root/sha256: ${n} 与台账摘要不符"
    done

    # 3) 注册表：每条 filename 都要落在台账内，且文件确实在树里。指向
    #    不存在的文件时装机前无人报错，桌面会话里只表现为空背景。
    have=$(grep -rhoE '<filename>[^<]+</filename>' "${reg}" 2>/dev/null |
        wc -l)
    [ "${have:-0}" -eq "${COUNT}" ] ||
        bad "root/registry-count: 应恰为 ${COUNT} 条（实为 ${have:-0}）"
    while IFS= read -r f; do
        has_name "${f##*/}" || bad "root/registry: ${f##*/} 不在台账内"
        [ -f "${r}${f}" ] || bad "root/registry-file: 缺少 ${f}"
    done < <(sed -n 's:.*<filename>\([^<]*\)</filename>.*:\1:p' \
        "${reg}"/*.xml 2>/dev/null)

    # 4) schema 默认值：装好的 override 必须仍指向台账内的一张。
    f=$(sed -n "s/^picture-filename='\([^']*\)'$/\1/p" \
        "${sch}"/*.override 2>/dev/null | head -n 1)
    [ -n "${f}" ] || bad "root/schema: 无 picture-filename 覆盖"
    [ -z "${f}" ] || has_name "${f##*/}" ||
        bad "root/schema: 默认值 ${f##*/} 不在台账内"
}

[ -n "${SOURCE}" ] && chk_src "${SOURCE}"
[ -n "${ROOT}" ] && chk_root "${ROOT}"

if [ "${FAILS}" -ne 0 ]; then
    err "内置壁纸集合失败 ${FAILS} 项"
    exit 1
fi

say "壁纸集合通过"
exit 0
