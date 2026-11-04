#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/alk-size.sh — 同配置构件的内核镜像体积守恒（ALK-01）
#
# 用法:
#   ci/gates/alk-size.sh --base <镜像|目录> --cand <镜像|目录>
#   ci/gates/alk-size.sh --base-size <字节数> --cand <镜像|目录>
#
# 0.0.4 的 ALK 铁律是「本轨给内核加的是用户态能力，不是内核体积」。体积
# 是否真的没变，不能靠人看构建日志，只能拿同配置的两份构件比：同一份
# defconfig、同一组源码，两次构建产出的内核镜像字节数必须相同。差 1 字节
# 就说明有东西进了内核镜像，无论它来自配置、源码还是打包。
#
# 两份输入的形态都允许是「镜像文件」或「构建目录」：不同架构的产物落在
# 不同位置（x86 是 arch/x86/boot/bzImage，arm64/riscv 是 arch/<arch>/
# boot/Image，发行包则是 /boot/vmlinuz-<release>），逐个试固定名字比让
# 调用方自己拼路径更不容易错。
#
# 退出码: 0 体积相同；1 体积不同；5 参数不满足或找不到镜像。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

BASE=""
BASE_SIZE=""
CAND=""

usage()
{
    cat <<'EOF'
用法: ci/gates/alk-size.sh --base <镜像|目录> --cand <镜像|目录>
      ci/gates/alk-size.sh --base-size <字节数> --cand <镜像|目录>

  --base       基准构件：镜像文件或构建目录
  --base-size  只在台账里记了字节数时的基准值
  --cand       待比构件：镜像文件或构建目录
  -h, --help   显示本帮助
EOF
}

parse_args()
{
    while [ "$#" -gt 0 ]; do
        case "$1" in
        --base) BASE="${2:-}"; shift 2 || shift ;;
        --base-size) BASE_SIZE="${2:-}"; shift 2 || shift ;;
        --cand) CAND="${2:-}"; shift 2 || shift ;;
        -h | --help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage >&2; exit "${EINVAL}" ;;
        esac
    done
    [ -n "${CAND}" ] || { err "缺少 --cand"; exit "${EINVAL}"; }
    if [ -z "${BASE}" ] && [ -z "${BASE_SIZE}" ]; then
        err "缺少 --base 或 --base-size"
        exit "${EINVAL}"
    fi
}

# 镜像名按架构产物与发行包两种布局依次尝试；-f 判定的是普通文件，
# 目录同名项（如 arch/x86/boot/ 下的目录）不会被误当镜像。
locate()
{
    local p="$1" name

    [ -f "${p}" ] && { printf '%s\n' "${p}"; return 0; }
    [ -d "${p}" ] || return 1
    for name in vmlinuz bzImage Image arch/x86/boot/bzImage \
        arch/arm64/boot/Image arch/riscv/boot/Image; do
        [ -f "${p}/${name}" ] && { printf '%s\n' "${p}/${name}"; return 0; }
    done
    for name in "${p}"/boot/vmlinuz-*; do
        [ -f "${name}" ] && { printf '%s\n' "${name}"; return 0; }
    done
    return 1
}

size_of()
{
    stat -c %s "$1" 2>/dev/null || stat -f %z "$1" 2>/dev/null
}

main()
{
    local image have diff

    parse_args "$@"
    image="$(locate "${CAND}")" || {
        err "找不到内核镜像: ${CAND}"
        exit "${EINVAL}"
    }
    if [ -z "${BASE_SIZE}" ]; then
        BASE="$(locate "${BASE}")" || {
            err "找不到基准镜像: ${BASE}"
            exit "${EINVAL}"
        }
        BASE_SIZE="$(size_of "${BASE}")" || {
            err "无法读取基准体积: ${BASE}"
            exit "${EINVAL}"
        }
    fi
    have="$(size_of "${image}")" || {
        err "无法读取构件体积: ${image}"
        exit "${EINVAL}"
    }
    diff=$((have - BASE_SIZE))
    say "base=${BASE_SIZE} cand=${have} diff=${diff} image=${image#"${ROOT}/"}"
    if [ "${diff}" -ne 0 ]; then
        err "ALK 体积不守恒: 差 ${diff} 字节"
        exit 1
    fi
    say "== alk-size passed =="
}

main "$@"
