#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-config.sh — airy_defconfig 快照（发布物料 6）
#
# 用法: ci/gates/release-config.sh snapshot --ver VER [--src DIR] [--out DIR]
#
# 快照逐字节取自内核子仓 arch/<arch>/configs/airy_defconfig，四个交付架构
# 各一份，不重新渲染、不追加注释：这份物料的价值是「与打内核 RPM 时用的是
# 同一份字节」，改写过的快照无法与发行包里的配置互证。README 说明合并方式，
# 取件方据此在本机复现同一份 .config。
#
# 选型配置（09-release-process.md §3.5）逐项断言在每个架构的片段里：io_uring
# 打开、纯 C LSM（SECURITY_AIRY）打开、BPF LSM 关闭。§3.5 写的
# CONFIG_SECURITY_AIRY_LSM 在 6.6 基线里没有这个符号（实际符号为
# CONFIG_SECURITY_AIRY），SCHED_EXT 是 6.12 才引入的配置项（6.6 里恒不存在）。
# 这里断言真实符号、并拒绝任何启用 sched_ext 的片段，而不是拿不存在的符号
# 写一条恒真的检查——后者在配置被改坏时照样绿。
#
# 确定性：tar 按名排序、mtime 取 SOURCE_DATE_EPOCH、属主归零、xz 单线程，
# 打包两遍逐字节比对。同一份源码两次打包不同，摘要清单在重发时必然对不上。
#
# 退出码: 0 通过；1 快照或断言失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# 架构对：发行架构:内核树 arch 目录名。四个架构就是 §3.3 内核 RPM 的四腿，
# 少一腿即快照不覆盖本版内核包。
readonly ARCHES=(
    'x86_64:x86'
    'aarch64:arm64'
    'riscv64:riscv'
    'loongarch64:loongarch'
)
# 片段里必须逐字出现的选型配置。
readonly REQUIRED=(
    'CONFIG_IO_URING=y'
    'CONFIG_SECURITY_AIRY=y'
    '# CONFIG_BPF_LSM is not set'
)
readonly SCHED_EXT='CONFIG_SCHED_EXT=y'
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${ROOT}/kernel"
OUT="."
VER=""
epoch=0
# 暂存树与孪生树在打包结束后必须清掉：源树里留一份几十 MB 的未打包副本，
# 下一次打包就会把它算进产物。目录名用全局变量，退出钩子才能在异常退出
# 时也拿到它。
STAGE=""
TWIN=""

cleanup()
{
    [ -z "${STAGE}" ] || rm -rf "${STAGE}"
    [ -z "${TWIN}" ] || rm -rf "${TWIN}"
}

trap cleanup EXIT

usage()
{
    sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"
}

check_frag()
{
    local f="$1" arch="$2" want

    [ -s "${f}" ] || die "${arch} 的 airy_defconfig 为空: ${f}"
    for want in "${REQUIRED[@]}"; do
        grep -qxF "${want}" "${f}" ||
            die "${arch} 的 airy_defconfig 缺少「${want}」"
    done
    if grep -qxF "${SCHED_EXT}" "${f}"; then
        die "${arch} 的 airy_defconfig 启用了 sched_ext（本版选型为不使用）"
    fi
}

readme()
{
    cat >"$1" <<EOF
AirymaxOS airy_defconfig 快照（v${VER}）

每个 <arch>-airy_defconfig 是内核树 arch/<arch>/configs/airy_defconfig 的
逐字节副本，与 v${VER} 发行内核 RPM 构建时所用配置同源。合并方式：

  make ARCH=<arch> defconfig
  scripts/kconfig/merge_config.sh -m .config <arch>-airy_defconfig
  make ARCH=<arch> olddefconfig
EOF
}

stage()
{
    local dir="$1" item arch kdir src

    mkdir -p "${dir}" || die "无法创建暂存目录: ${dir}"
    for item in "${ARCHES[@]}"; do
        arch="${item%%:*}"
        kdir="${item##*:}"
        src="${SRC}/arch/${kdir}/configs/airy_defconfig"
        [ -f "${src}" ] || die "缺少 ${src}（内核子模块未初始化？）"
        check_frag "${src}" "${arch}"
        cp "${src}" "${dir}/${arch}-airy_defconfig" ||
            die "复制失败: ${arch}"
    done
    readme "${dir}/README" || die "写入 README 失败"
}

build_tar()
{
    local dir="$1" dest="$2" names=()

    mapfile -t names < <(find "${dir}" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
    [ "${#names[@]}" -gt 0 ] || die "暂存目录为空: ${dir}"
    tar --format=gnu --sort=name --mtime="@${epoch}" \
        --owner=0 --group=0 --numeric-owner \
        -cf - -C "${dir}" "${names[@]}" | xz -T1 -9 >"${dest}" ||
        die "打包失败: ${dest}"
}

verify_tar()
{
    local f="$1" item arch want=() got

    for item in "${ARCHES[@]}"; do
        want+=("${item%%:*}-airy_defconfig")
    done
    want+=('README')
    got="$(tar -tf "${f}" | sort)"
    if [ "${got}" != "$(printf '%s\n' "${want[@]}" | sort)" ]; then
        printf '%s\n' "${got}" >&2
        die "快照内容与预期不符: ${f}"
    fi
}

snapshot()
{
    local name="agent-linux-${VER}-airy-defconfig.tar.xz"

    [ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}"
    epoch="${SOURCE_DATE_EPOCH:-0}"
    case "${epoch}" in
        ''|*[!0-9]*) die "SOURCE_DATE_EPOCH 须为非负整数: ${epoch}" ;;
    esac
    mkdir -p "${OUT}" || die "无法创建输出目录: ${OUT}"

    STAGE="$(mktemp -d)"
    TWIN="$(mktemp -d)"

    group "Stage airy_defconfig"
    stage "${STAGE}" || exit 1
    say "已收录 $(( ${#ARCHES[@]} + 1 )) 个文件: ${#ARCHES[@]} 份配置 + README"
    endgroup

    group "Pack snapshot"
    build_tar "${STAGE}" "${OUT}/${name}" || exit 1
    build_tar "${STAGE}" "${TWIN}/${name}" || exit 1
    cmp -s "${OUT}/${name}" "${TWIN}/${name}" ||
        die "${name} 两次打包不一致（确定性被破坏）"
    verify_tar "${OUT}/${name}" || exit 1
    say "${name} $(wc -c <"${OUT}/${name}") 字节 $(sha256sum "${OUT}/${name}" | cut -d' ' -f1)"
    endgroup
}

cmd="${1:-}"
[ "$#" -gt 0 ] || { usage >&2; exit "${EINVAL}"; }
shift

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ver)     VER="${2:-}"; shift 2 ;;
        --src)     SRC="${2:-}"; shift 2 ;;
        --out)     OUT="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

case "${cmd}" in
    snapshot)
        [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
        need_cmd tar xz sha256sum cmp
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
