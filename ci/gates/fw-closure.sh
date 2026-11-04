#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/fw-closure.sh — 固件闭包判定（QG-10 / D-01 / FW-01）
#
# 用法: ci/gates/fw-closure.sh [--root DIR] [--kernel DIR --drv NAME]
#       [--manifest FILE] [--comps FILE]
#
# 六件事，按证据由弱到强排列：
#   ① 集合对账（构建期）：comps 里名称含 firmware 或以 -ucode 结尾的包，
#      必须全部出现在 fw-manifest.txt；清单声明的包必须全部由 comps 安装。
#      两个方向都判，是因为少声明会让器件在装机后静默失效，多声明则让
#      清单慢慢变成与镜像无关的愿望列表。
#   ② 实物核对（运行期证据）：给 --root 时逐条模式在系统内至少命中一个
#      文件。包在而内容不在，正是 D-01「构建期与运行期双判」要拦的形态。
#   ③ 内核 API 对账（构建期，需 --kernel 与 --root）：驱动经
#      `modinfo -F firmware` 声明的每个固件名，必须在固件树里存在（含
#      .xz/.zst 变体）。0.0.3 的 BE200 要 gl-c0-fm-c0（API 83），镜像只有
#      -92/-100/-101，两个集合无交集，装机后才发现；这一项把缺口提前到
#      出厂前。驱动名由调用方给出而不从清单推断：清单的粒度是包，映射到
#      驱动需要内核侧事实，猜出来的映射会掩盖真实缺口。
#   ④ .pnvm 与 .ucode 成对：pnvm 是 ucode 的伴生能力描述，只有 pnvm 没有
#      ucode 时固件载入失败，dmesg 只留一句 api flags 越界，排障无从下手。
#      反方向不判——没有 pnvm 的老器件是正常形态。
#   ⑤ SELinux 标签（需 --root 与本机策略）：清单命中的固件文件必须落在
#      lib_t。内核读固件只要求该标签，enforcing 下标签错了表现为「文件在
#      但载入失败」。本机无 matchpathcon 或无策略时只提示不判负——宿主
#      环境的缺失不是镜像的缺陷。
#   ⑥ 清单回读（需 --root）：镜像内的 fw-manifest.txt 必须与仓库清单逐字
#      相同，同目录还要有内核 ABI 标记。清单只留在仓库里时，目标机上的
#      判定只能拿发行当天的旧清单套升级后的镜像，两边分叉而判定仍绿；
#      这一项保证「按同一份数据判」在装机后也成立。
#
# 谓词只认「是不是固件包」，不认具体包名：仓库元数据在离线环境不可得，
# 而 comps 是「镜像装什么包」的唯一声明；新增固件包时判定自动生效，不用
# 改代码。
#
# 清单里为什么会有监管域包：wireless-regdb 的包名不含 firmware，谓词认不
# 出来，但它提供 /usr/lib/firmware/regulatory.db，缺失即 0.0.3 真机缺陷
# D-03。这类「名字不说自己是固件」的包由清单数据兜底，判定不留别名。
#
# 退出码: 0 判定通过；1 判定未通过；5 参数或证据不可用。
# ===========================================================================
set -uo pipefail

# 集合对账用 sort/comm，二者必须按同一码位序比较。宿主的 C.UTF-8 排序会
# 忽略大小写（NetworkManager 排到 n 段），comm 随即报「not in sorted
# order」并给出错误的差集；钉死 C 后同一份输入在任何机器上得到同一结论。
export LC_ALL=C

readonly EINVAL=5
readonly FWDIR=/usr/lib/firmware
# 清单与 ABI 标记的安装位置，由 distro/specs/airymaxos-firmware.spec 的
# %files 决定；改路径必须同时改两处，否则回读判定会在装机后判负。
readonly MDIR=/usr/share/airymaxos/firmware

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
MAN="${ROOT}/distro/firmware/fw-manifest.txt"
COMPS="${ROOT}/distro/comps/airymaxos-comps.xml"
SYSROOT=""
KERNEL=""
DRVS=()
# 两个外部命令都可被环境覆盖：宿主是否装了 kmod / libselinux 与本判定无关，
# 缺了要能如实报「证据不可用」，而不是让调用方去拼 PATH 才能复现分支。
MODBIN="${AIRY_MODINFO:-modinfo}"
SELBIN="${AIRY_MATCHPATHCON:-matchpathcon}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

usage()
{
    printf '用法: ci/gates/fw-closure.sh'
    printf ' [--root DIR] [--kernel DIR --drv NAME]'
    printf ' [--manifest FILE] [--comps FILE]\n'
}

# comp_pkgs FILE —— comps 内全部包名，一行一个，不去重（重复声明本身
# 由 comps_lint 判，这里保留原始形态）。用 grep -o 而不是按行取末尾，
# 是为了不依赖「一行一个 packagereq」的排版习惯。
comp_pkgs()
{
    grep -o '<packagereq[^>]*>[^<]*' "$1" | sed 's/.*>//'
}

# fw_pkgs FILE —— 固件包谓词：名称含 firmware，或以 -ucode 结尾。
fw_pkgs()
{
    comp_pkgs "$1" | grep -E 'firmware|-ucode$' || true
}

# rows FILE —— 去掉注释与空行后的数据行。
rows()
{
    sed 's/#.*//' "$1" | awk 'NF'
}

# row_check —— 逐行校验字段数、类别、路径前缀与上级引用，合格行写入
# data 供后续对账；置 bad=1 而不提前退出，让一轮跑完全部问题。
row_check()
{
    local n=0 pkg cls pat extra

    while read -r pkg cls pat extra; do
        n=$((n + 1))
        if [ -z "${pat}" ] || [ -n "${extra}" ]; then
            err "清单第 ${n} 行字段数不等于 3"
            bad=1
            continue
        fi
        case "${cls}" in
            ''|*[!a-z0-9-]*)
                err "清单第 ${n} 行器件类别非法: ${cls}"
                bad=1
                continue
                ;;
        esac
        case "${pat}" in
            "${FWDIR}"/*) ;;
            *)
                err "清单第 ${n} 行不在 ${FWDIR} 下: ${pat}"
                bad=1
                continue
                ;;
        esac
        case "${pat}" in
            *..*)
                err "清单第 ${n} 行含上级引用: ${pat}"
                bad=1
                continue
                ;;
        esac
        printf '%s\t%s\t%s\n' "${pkg}" "${cls}" "${pat}" >>"${TMP}/data"
    done <"${TMP}/rows"

    if [ "${n}" -eq 0 ]; then
        err "固件清单为空: ${MAN}"
        bad=1
    fi
}

# dup_check —— 同一 (包, 路径) 出现两次即判红：重复条目会让按器件汇总
# 的计数虚高，且两条中一条被删时另一条会掩盖缺口。
dup_check()
{
    local d

    while IFS= read -r d; do
        err "清单条目重复: ${d}"
        bad=1
    done < <(cut -f1,3 "${TMP}/data" | sort | uniq -d)
}

# set_check —— 双向对账。
set_check()
{
    local p

    cut -f1 "${TMP}/data" | sort -u >"${TMP}/man"
    comp_pkgs "${COMPS}" | sort -u >"${TMP}/all"
    fw_pkgs "${COMPS}" | sort -u >"${TMP}/fw"

    if [ ! -s "${TMP}/fw" ]; then
        err "comps 未声明固件包: ${COMPS}"
        bad=1
    fi

    while IFS= read -r p; do
        if [ -n "${p}" ]; then
            err "comps 固件包未在清单声明: ${p}"
            bad=1
        fi
    done < <(comm -23 "${TMP}/fw" "${TMP}/man")

    while IFS= read -r p; do
        if [ -n "${p}" ]; then
            err "清单包不在 comps: ${p}"
            bad=1
        fi
    done < <(comm -23 "${TMP}/man" "${TMP}/all")
}

# file_check —— 实物核对：每条路径模式在 --root 内至少命中一个文件。
file_check()
{
    local pkg cls pat

    while IFS=$'\t' read -r pkg cls pat; do
        if ! compgen -G "${SYSROOT}${pat}" >/dev/null; then
            err "固件缺失 [${cls}] ${pat}"
            bad=1
        fi
    done <"${TMP}/data"
}

# read_back —— 清单回读：镜像内的清单必须与仓库清单逐字相同。
#
# 只比「有没有」不够：装机后跑判定时，调用方拿的仍是仓库里的清单，若镜像
# 内那份已随包升级而变，比对的其实是两份不同代的数据。逐字相等把这件事
# 变成一次 cmp，差异直接判负。
read_back()
{
    local dst="${SYSROOT}${MDIR}/fw-manifest.txt"
    local abi="${SYSROOT}${MDIR}/kernel-abi"

    if [ ! -r "${dst}" ]; then
        err "镜像内无固件清单: ${MDIR}/fw-manifest.txt"
        bad=1
        return 0
    fi
    if ! cmp -s "${MAN}" "${dst}"; then
        err "镜像内固件清单与仓库清单不一致: ${MDIR}/fw-manifest.txt"
        bad=1
    fi
    if [ ! -r "${abi}" ]; then
        err "镜像内无内核 ABI 标记: ${MDIR}/kernel-abi"
        bad=1
        return 0
    fi
    # ABI 形如 6.6.161：只允许数字与点。空串或带其它字符说明包装错了版本。
    case "$(tr -d '[:space:]' <"${abi}")" in
    ''|*[!0-9.]*)
        err "内核 ABI 标记非法: ${MDIR}/kernel-abi"
        bad=1
        ;;
    esac
}

# ko_path NAME —— 驱动名到模块文件。压缩后缀由发行版决定，先按三种常见
# 形态直接命中，再退回子树搜索，避免调用方必须先知道模块装在哪一层。
ko_path()
{
    local d

    for d in "${KERNEL}/${1}.ko" "${KERNEL}/${1}.ko.xz" \
             "${KERNEL}/${1}.ko.zst"; do
        if [ -f "${d}" ]; then
            printf '%s' "${d}"
            return 0
        fi
    done
    find "${KERNEL}" -type f -name "${1}.ko*" -print -quit 2>/dev/null
}

# fw_names KO —— 模块声明的固件名，一行一个。modinfo 对多值条目按行输出，
# 这里再用 tr 兜一层，避免某一版 kmod 把多个名字写在同一行时漏判。
fw_names()
{
    "${MODBIN}" -F firmware "$1" 2>/dev/null | tr -s '[:space:]' '\n' | awk 'NF'
}

# fw_have NAME —— 固件树内是否存在该文件（含 .xz/.zst 变体）。
fw_have()
{
    local p="${SYSROOT}${FWDIR}/$1"

    [ -f "${p}" ] || [ -f "${p}.xz" ] || [ -f "${p}.zst" ]
}

# api_check —— 逐个驱动核对固件依赖。驱动不在模块树里判负：那说明调用方
# 给的驱动名与出货模块集不一致，此时「没报缺失」是假绿，比判负更危险。
api_check()
{
    local drv ko name

    for drv in "${DRVS[@]}"; do
        ko="$(ko_path "${drv}")"
        if [ -z "${ko}" ]; then
            err "模块树内无此驱动: ${drv}"
            bad=1
            continue
        fi
        while IFS= read -r name; do
            fw_have "${name}" && continue
            err "驱动固件缺失 [${drv}] ${name}"
            bad=1
        done < <(fw_names "${ko}")
    done
}

# pnvm_check —— 每个 .pnvm 必须有同族的 .ucode。pnvm 携带能力描述，内核
# 先读 ucode 再按 pnvm 协商；缺 ucode 时两者都不生效。同族指同一 stem，
# 或 stem 后接 -API 的版本名——iwlwifi 的 pnvm 不带 API 号，ucode 带，
# 只按完全同名判对会把正常布局判成缺文件。
pnvm_check()
{
    local f stem

    while IFS= read -r f; do
        stem="${f%.pnvm*}"
        if compgen -G "${stem}.ucode*" >/dev/null \
            || compgen -G "${stem}-*.ucode*" >/dev/null; then
            continue
        fi
        err "伴生 ucode 缺失: ${f#"${SYSROOT}"}"
        bad=1
    done < <(find "${SYSROOT}${FWDIR}" -type f \
        \( -name '*.pnvm' -o -name '*.pnvm.xz' -o -name '*.pnvm.zst' \) \
        2>/dev/null)
}

# sel_lab PATH —— 策略给出的标签；无策略时为空或 <<none>>。
sel_lab()
{
    "${SELBIN}" -n "$1" 2>/dev/null | awk -F'\t' 'NR == 1 { print $2 }'
}

# policy_ok —— 宿主是否真的装着策略。只看命令是否存在不够：容器里常有
# libselinux 却没策略文件，此时每个路径都得到 <<none>>，逐条判负会把宿主
# 缺口当成镜像缺陷。用固件树根做哨兵，判的是「本机能回答这个问题」。
policy_ok()
{
    local lab

    lab="$(sel_lab "${FWDIR}")"
    [ -n "${lab}" ] && [ "${lab}" != "<<none>>" ]
}

# sel_check —— 清单命中的固件文件必须落在 lib_t。内核读固件不经过 DAC，
# 标签错了在 enforcing 下就是「文件在、载入失败」，且无任何指向性告警。
# 查的是镜像内路径而不是宿主路径：--root 只是为了取出文件，策略问的是
# 文件装到镜像里的位置，拿 /tmp/xxx 前缀去问只会得到 default_t。
sel_check()
{
    local pkg cls pat f lab

    while IFS=$'\t' read -r pkg cls pat; do
        while IFS= read -r f; do
            lab="$(sel_lab "${f#"${SYSROOT}"}")"
            case "${lab}" in
            *:lib_t|*:lib_t:*) ;;
            *)
                err "固件标签非 lib_t: ${f#"${SYSROOT}"} → ${lab:-（无）}"
                bad=1
                ;;
            esac
        done < <(compgen -G "${SYSROOT}${pat}")
    done <"${TMP}/data"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --manifest)
            [ -n "${2:-}" ] || { err "--manifest 需要文件路径"; exit "${EINVAL}"; }
            MAN="$2"
            shift 2
            ;;
        --comps)
            [ -n "${2:-}" ] || { err "--comps 需要文件路径"; exit "${EINVAL}"; }
            COMPS="$2"
            shift 2
            ;;
        --root)
            [ -n "${2:-}" ] || { err "--root 需要目录路径"; exit "${EINVAL}"; }
            SYSROOT="$2"
            shift 2
            ;;
        --kernel)
            [ -n "${2:-}" ] || { err "--kernel 需要目录路径"; exit "${EINVAL}"; }
            KERNEL="$2"
            shift 2
            ;;
        --drv)
            [ -n "${2:-}" ] || { err "--drv 需要驱动名"; exit "${EINVAL}"; }
            DRVS+=("$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "未知参数: $1"
            exit "${EINVAL}"
            ;;
    esac
done

[ -r "${MAN}" ] || { err "清单不可读: ${MAN}"; exit "${EINVAL}"; }
[ -r "${COMPS}" ] || { err "comps 不可读: ${COMPS}"; exit "${EINVAL}"; }
if [ -n "${SYSROOT}" ] && [ ! -d "${SYSROOT}${FWDIR}" ]; then
    err "根目录下无固件树: ${SYSROOT}${FWDIR}"
    exit "${EINVAL}"
fi

# 内核 API 对账的三项前提缺一不可：没有模块树读不出驱动声明，没有镜像根
# 无从求差集，没有驱动名则判据为空——空判据恒绿，比不跑更误导。
if [ -n "${KERNEL}" ]; then
    [ -d "${KERNEL}" ] || { err "模块树不存在: ${KERNEL}"; exit "${EINVAL}"; }
    [ -n "${SYSROOT}" ] || { err "--kernel 需要同时给 --root"; exit "${EINVAL}"; }
    [ "${#DRVS[@]}" -gt 0 ] || { err "--kernel 需要至少一个 --drv"; exit "${EINVAL}"; }
    command -v "${MODBIN}" >/dev/null 2>&1 || {
        err "缺少 ${MODBIN}，读不出驱动的固件依赖"
        exit "${EINVAL}"
    }
elif [ "${#DRVS[@]}" -gt 0 ]; then
    err "--drv 需要同时给 --kernel 与 --root"
    exit "${EINVAL}"
fi

bad=0
group "固件闭包"

rows "${MAN}" >"${TMP}/rows"
: >"${TMP}/data"
row_check
dup_check
set_check
[ -z "${SYSROOT}" ] || { file_check; read_back; }

if [ -n "${SYSROOT}" ]; then
    pnvm_check
    if ! command -v "${SELBIN}" >/dev/null 2>&1; then
        note "无 ${SELBIN}，跳过固件标签断言"
    elif policy_ok; then
        sel_check
    else
        note "本机无 SELinux 策略，跳过固件标签断言"
    fi
fi

[ -z "${KERNEL}" ] || api_check

row_n="$(wc -l <"${TMP}/data" | tr -d '[:space:]')"
man_n="$(wc -l <"${TMP}/man" | tr -d '[:space:]')"
fw_n="$(wc -l <"${TMP}/fw" | tr -d '[:space:]')"

if [ "${bad}" -ne 0 ]; then
    die "固件闭包判定未通过（条目 ${row_n}，包 ${man_n}，comps 固件包 ${fw_n}）"
fi

say "固件闭包判定通过: 条目 ${row_n}，包 ${man_n}，comps 固件包 ${fw_n}"
[ -z "${KERNEL}" ] || say "内核固件依赖核对: 驱动 ${#DRVS[@]}"
endgroup
