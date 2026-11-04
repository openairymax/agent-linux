#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-source.sh — 源码 tarball（发布物料 1）
#
# 用法: ci/gates/release-source.sh snapshot --ver VER [--repo DIR] [--out DIR]
#
# 09-release-process.md §3.2 要求 tarball 含「8 子仓的源码 + 文档 + 构建脚本」，
# 但同节给出的 `git archive vX.Y.Z` 只导出超项目一棵树——子仓在超项目里是
# gitlink，不带 --recurse-submodules（git archive 并不支持）就拿不到子仓源码。
# 照抄那条命令会产出一个「看起来是源码包、实际缺内核与全部用户态仓」的归档，
# 且校验和、签名、发布页全都正常，只有取件方解包后才发现。本门禁按 gitlink
# 记录逐个 `git archive`，用子仓自己记录的提交号导出，取什么提交由超项目
# 的 index 决定，与工作树里子仓当前检出状态无关。
#
# 归档成员：
#   agent-linux-<ver>/                    超项目 HEAD
#   agent-linux-<ver>/<path>/             每个子仓，按 gitlink 提交导出
#   agent-linux-<ver>/SOURCES             超项目与各子仓的提交号清单
#
# 确定性：暂存树先落地，再用 tar 的排序与归零参数重新打包，mtime 统一取
# SOURCE_DATE_EPOCH；xz 用固定并行度与预设（-6 -T4）。同一份提交在同一
# 镜像上两次打包逐字节相同。并行度显式固定而非 -T0：机器核数变化会让块
# 切分变化，产物就不再可复现。
#
# 必须落到暂存树再打包，不能把几段 `git archive` 的 tar 直接首尾相接拼进
# xz：每段自带 EOF 零块，`tar -tf`、`tar -xf` 读到第一段末尾就停，用户
# 解包只能拿到超项目那棵树，子仓源码明明在文件里却取不出来，而校验和与
# 签名全都正常。单份归档由最后一次 tar 生成，只有这一种形态对取件方成立。
#
# 退出码: 0 通过；1 导出或自证失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly XZ_PRESET='-6'
readonly XZ_JOBS='4'
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
REPO="${ROOT}"
OUT="."
VER=""
epoch=0
WORK=""

cleanup()
{
    [ -z "${WORK}" ] || rm -rf "${WORK}"
}

trap cleanup EXIT

usage()
{
    sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"
}

# subs —— 打印「状态 提交 路径」。git submodule status 用首字符表示状态：
# 空格为正常，`+` 为工作树与 index 不同（导出仍取 index 的提交，故放行），
# `-` 为未初始化、`U` 为冲突——后两者都无法导出可信源码，一律判负。
# 空格状态改写为 ok：read 会把首列的前导空白吃掉，不改写就整体错位一列。
subs()
{
    git -C "${REPO}" submodule status --recursive |
        awk '{
                 mark = substr($0, 1, 1)
                 if (mark == " ") { print "ok", $1, $2 }
                 else { print mark, substr($1, 2), $2 }
             }'
}

check_subs()
{
    local mark sha path count=0

    while read -r mark sha path; do
        case "${mark}" in
            ok|+) ;;
            -) die "子仓未初始化: ${path}（导出会缺源码）" ;;
            U) die "子仓有未解决的冲突: ${path}" ;;
            *) die "无法识别的子仓状态「${mark}」: ${path}" ;;
        esac
        [ -n "${path}" ] || die "子仓清单里有空路径"
        count=$((count + 1))
    done < <(subs)
    [ "${count}" -gt 0 ] || die "超项目没有任何子仓，源码包会缺全部子仓源码"
}

# sources —— 提交号清单：解包方据此核对「这份源码是不是 tag 指向的那一份」。
sources()
{
    printf 'agent-linux %s\n' "$(git -C "${REPO}" rev-parse HEAD)" >"$1"
    subs | while read -r mark sha path; do
        printf '%s %s\n' "${sha}" "${path}"
    done >>"$1"
}

# stage —— 把超项目与各子仓按 gitlink 提交导出到同一棵暂存树。两段导出
# 共用同一个 prefix，解包后超项目文件与子仓目录自然落在同一层级。
stage()
{
    local dir="$1" prefix="$2" mark sha path

    mkdir -p "${dir}" || die "无法创建暂存目录: ${dir}"
    git -C "${REPO}" archive --format=tar --prefix="${prefix}/" HEAD |
        tar -xf - -C "${dir}" || die "超项目导出失败"
    while read -r mark sha path; do
        git -C "${REPO}/${path}" archive --format=tar \
            --prefix="${prefix}/${path}/" "${sha}" |
            tar -xf - -C "${dir}" || die "子仓导出失败: ${path} ${sha:0:12}"
    done < <(subs)
    sources "${dir}/${prefix}/SOURCES" || die "写入 SOURCES 失败"
}

pack()
{
    local dest="$1" ver="$2"
    local prefix="agent-linux-${ver}"
    local dir="${WORK}/stage"

    stage "${dir}" "${prefix}" || return 1
    tar --format=gnu --sort=name --mtime="@${epoch}" \
        --owner=0 --group=0 --numeric-owner \
        -cf - -C "${dir}" "${prefix}" |
        xz "${XZ_PRESET}" -T"${XZ_JOBS}" >"${dest}" || die "打包失败: ${dest}"
}

# verify —— 一次解包列举完成全部自证：哨兵文件、`.git` 残留、每条子仓路径
# 是否真的带出文件。列举落临时文件后复用，避免反复解压 1.4 GB。
#
# 子仓只看「目录存在」不够：git archive 对 gitlink 会输出一个空目录条目，
# 上一版正是这样「看起来有 kernel/」而实际没有内核源码。这里数的是该路径
# 下不以 `/` 结尾的成员，即真实文件。
verify()
{
    local f="$1" ver="$2" list count mark sha path files
    local prefix="agent-linux-${ver}"

    list="${WORK}/list"
    tar -tf "${f}" >"${list}" || die "归档不可读: ${f}"
    count="$(wc -l <"${list}")"

    for path in Makefile ci/ci.sh kernel/Makefile \
                kernel/include/linux/sched.h SOURCES; do
        grep -qxF "${prefix}/${path}" "${list}" ||
            die "归档缺少 ${prefix}/${path}"
    done
    if grep -q '/\.git/' "${list}"; then
        die "归档混入 .git 目录"
    fi
    while read -r mark sha path; do
        files="$(awk -v p="${prefix}/${path}/" \
            'index($0, p) == 1 && substr($0, length($0)) != "/" { n++ }
             END { print n + 0 }' "${list}")"
        [ "${files}" -gt 0 ] ||
            die "子仓 ${path} 只导出了空目录，没有源码"
    done < <(subs)
    say "自证通过：${count} 个成员，$(( $(subs | wc -l) )) 个子仓均带源码"
}

snapshot()
{
    local name="agent-linux-${VER}.tar.xz"

    [ -d "${REPO}/.git" ] || [ -f "${REPO}/.git" ] ||
        die "不是 git 仓库: ${REPO}"
    epoch="${SOURCE_DATE_EPOCH:-0}"
    case "${epoch}" in
        ''|*[!0-9]*) die "SOURCE_DATE_EPOCH 须为非负整数: ${epoch}" ;;
    esac
    mkdir -p "${OUT}" || die "无法创建输出目录: ${OUT}"
    WORK="$(mktemp -d)"

    group "Check submodules"
    check_subs
    say "超项目 $(git -C "${REPO}" rev-parse --short HEAD) + $(( $(subs | wc -l) )) 个子仓"
    endgroup

    mkdir -p "${WORK}"

    group "Export + compress"
    pack "${OUT}/${name}" "${VER}" || exit 1
    say "${name} $(wc -c <"${OUT}/${name}") 字节"
    endgroup

    group "Verify archive"
    verify "${OUT}/${name}" "${VER}" || exit 1
    say "sha256 $(sha256sum "${OUT}/${name}" | cut -d' ' -f1)"
    endgroup
}

cmd="${1:-}"
[ "$#" -gt 0 ] || { usage >&2; exit "${EINVAL}"; }
shift

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ver)     VER="${2:-}"; shift 2 ;;
        --repo)    REPO="${2:-}"; shift 2 ;;
        --out)     OUT="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

case "${cmd}" in
    snapshot)
        [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
        need_cmd git tar xz sha256sum
        snapshot
        ;;
    -h|--help)
        usage
        ;;
    *)
        err "未知子命令: ${cmd}"
        usage >&2
        exit "${EINVAL}"
        ;;
esac
