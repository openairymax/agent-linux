#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-verify.sh — 发布前的物料自证与发行自证
#
# 用法:
#   ci/gates/release-verify.sh [--dir DIR]
#   ci/gates/release-verify.sh (--iso FILE | --iso-dir DIR)
#                              --payload FILE --kernel-rpms DIR [--spec FILE]
#
# 两种模式互斥，由参数决定：
#
# 一、物料自证（--dir，缺省 "."）
#   时机在 artifact 落盘之后、推 registry 与建 Release 之前。上游签名发生在
#   另一台 runner、另一份文件系统；此处重算摘要，失败即说明落盘字节已被
#   artifact 打包/解包改写，继续发布等于把未经核对的东西挂上发行 tag。
#     * 摘要清单里每一行的文件都能找到，且重算摘要与清单一致
#     * 清单内每份署名非空，且其被签文件在物料里存在
#     * 清单内的发行公钥非空（公钥块不带被签文件，不能按署名要求）
#     * release notes 恰好三个 ## 小节，正文不超过 12 行
#
# 二、发行自证（--iso/--iso-dir + --payload + --kernel-rpms）
#   回答「这张 ISO 里的内核，是不是本版 %release 构建出来的那一份」。0.0.3
#   的重演故障是：已装机 ISO 的内核 NEVRA 为 0.0.3-2，而同一棵树的构建产物
#   是 0.0.3-5——镜像与发行包各自成立，却对不上同一版。两侧同时给出并逐字
#   相等，才允许挂上发行 tag（0.0.4 J-13 / QG-12）。
#     * ISO 摘要与其同名 .sha256 逐字一致（文件名也须一致）
#     * 载荷清单里每个内核包都在发行目录中存在，且 sha256 相等
#     * 内核主包、核心包、模块包三件齐备（对齐安装闭包）
#     * 主包 NEVRA 的 version 等于 spec 的 airy_version，release 等于
#       airy_release（其后可带 %dist 后缀）
#   缺任一侧即退 5：没有 ISO 摘要或没有发行内核包时「对照」不成立，不得降级
#   为「只验一侧就算过」。
#
# --payload 显式传入而不从 ISO 里现取：宿主机没有 xorriso；ISO 内
# /airymax/payload-manifest.txt 与这份源文件字节相等，已由组装期
# build-desktop.sh check_payload() 提取后 cmp 断言过。
#
# 不做 gpg --verify：发布 job 不导入私钥，keyring 里没有公钥。密码学校验
# 由取件方用发布页公钥完成（tools/airy-flash/README.md）。
#
# 退出码: 0 通过；1 判定不通过；5 参数不满足或证据不可用。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly NOTES_MAX=12
readonly KPKG="airymaxos-kernel"
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

DIR="."
DIR_SET=0
ISO=""
ISO_DIR=""
PAYLOAD=""
KRPM=""
SPEC=""
ISO_SUM=""
SPEC_VER=""
SPEC_REL=""
MAIN=""
declare -A PAY_SUM=()
declare -A RPM_SUM=()

usage()
{
    cat <<'EOF'
用法: ci/gates/release-verify.sh [--dir DIR]
      ci/gates/release-verify.sh (--iso FILE | --iso-dir DIR)
                                 --payload FILE --kernel-rpms DIR
                                 [--spec FILE]
EOF
}

need_arg()
{
    [ -n "${2:-}" ] || { err "$1 需要参数值"; exit "${EINVAL}"; }
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)         need_arg "$1" "${2:-}"; DIR="$2"; DIR_SET=1; shift 2 ;;
        --iso)         need_arg "$1" "${2:-}"; ISO="$2"; shift 2 ;;
        --iso-dir)     need_arg "$1" "${2:-}"; ISO_DIR="$2"; shift 2 ;;
        --payload)     need_arg "$1" "${2:-}"; PAYLOAD="$2"; shift 2 ;;
        --kernel-rpms) need_arg "$1" "${2:-}"; KRPM="$2"; shift 2 ;;
        --spec)        need_arg "$1" "${2:-}"; SPEC="$2"; shift 2 ;;
        -h|--help)     usage; exit 0 ;;
        *)             err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -z "${ISO}" ] || [ -z "${ISO_DIR}" ] ||
    { err "--iso 与 --iso-dir 只能取一个"; exit "${EINVAL}"; }

# .git 里不会有受签物料，剪掉它只为一件事：让 find 在 checkout 上不必
# 遍历对象库。
walk()
{
    find "${DIR}" -name .git -prune -o -type f "$@" -print
}

pick()
{
    local pattern="$1" hit
    hit="$(walk -name "${pattern}" | sort)"
    [ -n "${hit}" ] || { err "缺少${2}: ${pattern}"; return 1; }
    [ "$(printf '%s\n' "${hit}" | wc -l)" -eq 1 ] ||
        { err "${2}不唯一: ${pattern}"; return 1; }
    printf '%s\n' "${hit}"
}

material()
{
    local sum notes count=0 signs=0 keys=0 digest name hit actual f sections lines

    [ -d "${DIR}" ] || { err "目录不存在: ${DIR}"; exit "${EINVAL}"; }

    sum="$(pick 'agent-linux-*.sha256sum' '摘要清单')" || exit 1
    notes="$(pick 'agent-linux-*-release-notes.md' 'release notes')" || exit 1

    while read -r digest name; do
        [ -n "${name:-}" ] || continue
        hit="$(walk -name "${name}" | head -n 1)"
        [ -n "${hit}" ] || die "清单内文件缺失: ${name}"
        actual="$(sha256sum "${hit}" | cut -d' ' -f1)"
        [ "${actual}" = "${digest}" ] || die "摘要不符: ${name}"
        count=$((count + 1))
        case "${name}" in
            *.asc)
                [ -s "${hit}" ] || die "空署名或空公钥: ${name}"
                # 公钥块与分离署名同用 .asc 后缀，只能按内容分：
                # 公钥没有也不该有「被签文件」，按署名判会把它误判成缺件。
                if head -n 1 "${hit}" | grep -q 'BEGIN PGP PUBLIC KEY BLOCK'; then
                    keys=$((keys + 1))
                else
                    [ -n "$(walk -name "${name%.asc}" | head -n 1)" ] ||
                        die "署名缺少被签文件: ${name}"
                    signs=$((signs + 1))
                fi
                ;;
        esac
    done < "${sum}"
    [ "${count}" -gt 0 ] || die "摘要清单为空: ${sum}"
    [ "${signs}" -gt 0 ] || die "清单内没有任何署名"

    for f in "${sum}.asc" "${notes}.asc"; do
        [ -s "${f}" ] || die "缺少署名: ${f##*/}"
    done

    sections="$(grep -c '^## ' "${notes}" || true)"
    [ "${sections}" -eq 3 ] || die "${notes##*/} 小节数 ${sections}，应为 3"
    lines="$(grep -cv '^$' "${notes}" || true)"
    [ "${lines}" -le "${NOTES_MAX}" ] ||
        die "${notes##*/} 正文 ${lines} 行，上限 ${NOTES_MAX}"

    say "物料自证通过: ${count} 项摘要, ${signs} 份署名, ${keys} 份公钥, notes ${lines} 行"
}

iso_pick()
{
    local hit

    if [ -n "${ISO_DIR}" ]; then
        [ -d "${ISO_DIR}" ] || { err "目录不存在: ${ISO_DIR}"; exit "${EINVAL}"; }
        hit="$(find "${ISO_DIR}" -maxdepth 1 -type f -name '*.iso' | sort)"
        [ -n "${hit}" ] || { err "目录内没有 ISO: ${ISO_DIR}"; exit "${EINVAL}"; }
        [ "$(printf '%s\n' "${hit}" | wc -l)" -eq 1 ] ||
            { err "目录内 ISO 不唯一: ${ISO_DIR}"; exit "${EINVAL}"; }
        ISO="${hit}"
    fi
    [ -n "${ISO}" ] || { err "缺少 --iso 或 --iso-dir"; exit "${EINVAL}"; }
    [ -f "${ISO}" ] || { err "ISO 不存在: ${ISO}"; exit "${EINVAL}"; }
}

iso_sum()
{
    local base want name got

    base="${ISO##*/}"
    [ -f "${ISO}.sha256" ] ||
        { err "缺少 ISO 摘要: ${base}.sha256"; exit "${EINVAL}"; }
    read -r want name _ <"${ISO}.sha256"
    [ -n "${want:-}" ] ||
        { err "ISO 摘要为空: ${base}.sha256"; exit "${EINVAL}"; }
    [ "${name:-}" = "${base}" ] ||
        { err "ISO 摘要指向 ${name:-<空>}，应为 ${base}"; return 1; }
    got="$(sha256sum "${ISO}" | cut -d' ' -f1)"
    [ "${got}" = "${want}" ] || { err "ISO 摘要不符: ${base}"; return 1; }
    ISO_SUM="${got}"
}

# spec_glob KEY —— 取 %global 定义的第三个字段。
spec_glob()
{
    awk -v k="$1" '$1 == "%global" && $2 == k { print $3; exit }' "${SPEC}"
}

spec_read()
{
    local ver rel

    [ -f "${SPEC}" ] || { err "缺少内核 spec: ${SPEC}"; exit "${EINVAL}"; }
    ver="$(spec_glob airy_version)"
    rel="$(spec_glob airy_release)"
    [ -n "${ver}" ] || { err "${SPEC##*/} 缺少 %global airy_version"; exit "${EINVAL}"; }
    [ -n "${rel}" ] || { err "${SPEC##*/} 缺少 %global airy_release"; exit "${EINVAL}"; }
    SPEC_VER="${ver}"
    SPEC_REL="${rel}"
}

# nvr_of NAME-VERSION-RELEASE.ARCH.rpm —— 输出 VERSION-RELEASE。
nvr_of()
{
    local base="${1##*/}"

    base="${base%.rpm}"
    base="${base%.*}"
    printf '%s\n' "${base#${KPKG}-}"
}

# leg_of PREFIX —— 载荷里以 PREFIX 开头的内核包个数。
leg_of()
{
    local pre="$1" name n=0

    for name in "${!PAY_SUM[@]}"; do
        case "${name}" in
            "${pre}"*) n=$(( n + 1 )) ;;
        esac
    done
    printf '%s\n' "${n}"
}

main_of()
{
    local name

    for name in "${!PAY_SUM[@]}"; do
        case "${name}" in
            "${KPKG}-"[0-9]*.rpm) printf '%s\n' "${name}"; return 0 ;;
        esac
    done
    return 1
}

load_rel()
{
    local f name

    [ -d "${KRPM}" ] || { err "目录不存在: ${KRPM}"; exit "${EINVAL}"; }
    while IFS= read -r f; do
        [ -n "${f}" ] || continue
        name="${f##*/}"
        RPM_SUM["${name}"]="$(sha256sum "${f}" | cut -d' ' -f1)"
    done < <(find "${KRPM}" -type f -name "${KPKG}-*.rpm" | sort)
    [ "${#RPM_SUM[@]}" -gt 0 ] ||
        { err "发行目录没有内核包: ${KRPM}"; exit "${EINVAL}"; }
}

load_pay()
{
    local kind sum name

    [ -f "${PAYLOAD}" ] || { err "载荷清单不存在: ${PAYLOAD}"; exit "${EINVAL}"; }
    while read -r kind sum name _; do
        [ "${kind:-}" = rpm ] || continue
        case "${name:-}" in
            "${KPKG}-"*.rpm) PAY_SUM["${name}"]="${sum}" ;;
        esac
    done <"${PAYLOAD}"
    [ "${#PAY_SUM[@]}" -gt 0 ] ||
        { err "载荷清单没有内核包: ${PAYLOAD}"; exit "${EINVAL}"; }
}

check_trio()
{
    local leg

    MAIN="$(main_of)" || { err "载荷缺内核主包: ${KPKG}"; return 1; }
    for leg in core modules; do
        [ "$(leg_of "${KPKG}-${leg}-")" -ge 1 ] ||
            { err "载荷缺内核 ${leg} 包"; return 1; }
    done
}

check_pay()
{
    local name

    for name in "${!PAY_SUM[@]}"; do
        [ -n "${RPM_SUM["${name}"]+set}" ] ||
            { err "发行目录缺内核包: ${name}"; return 1; }
        [ "${RPM_SUM["${name}"]}" = "${PAY_SUM["${name}"]}" ] ||
            { err "内核包摘要不符: ${name}"; return 1; }
    done
}

check_nvr()
{
    local nvr ver rel

    nvr="$(nvr_of "${MAIN}")"
    ver="${nvr%-*}"
    rel="${nvr#*-}"
    [ "${ver}" = "${SPEC_VER}" ] ||
        { err "内核版本 ${ver} 与 spec airy_version ${SPEC_VER} 不符"; return 1; }
    case "${rel}" in
        "${SPEC_REL}"|"${SPEC_REL}".*) ;;
        *) err "内核 release ${rel} 与 spec airy_release ${SPEC_REL} 不符"; return 1 ;;
    esac
}

report()
{
    local name

    printf 'iso  %s  %s\n' "${ISO_SUM}" "${ISO##*/}"
    for name in "${!PAY_SUM[@]}"; do
        printf 'rpm  %s  %s\n' "${PAY_SUM["${name}"]}" "${name}"
    done | sort
    say "发行自证通过: ISO ${ISO_SUM} ↔ 内核 ${MAIN%.rpm} 共 ${#PAY_SUM[@]} 包"
}

proof()
{
    [ -n "${PAYLOAD}" ] || { err "--payload 必填"; exit "${EINVAL}"; }
    [ -n "${KRPM}" ] || { err "--kernel-rpms 必填"; exit "${EINVAL}"; }
    [ -n "${SPEC}" ] || SPEC="${ROOT}/distro/specs/kernel/${KPKG}.spec"

    iso_pick
    iso_sum || exit 1
    spec_read
    load_rel
    load_pay
    check_trio || exit 1
    check_pay || exit 1
    check_nvr || exit 1
    report
}

if [ -n "${ISO}" ] || [ -n "${ISO_DIR}" ] || [ -n "${PAYLOAD}" ] ||
    [ -n "${KRPM}" ] || [ -n "${SPEC}" ]; then
    [ "${DIR_SET}" -eq 0 ] ||
        { err "--dir 与发行自证参数不可同用"; exit "${EINVAL}"; }
    proof
else
    material
fi
