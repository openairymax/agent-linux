#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/fw-preflight.sh — 按架构预演固件扁平入口
#
# 用法:
#   ci/gates/fw-preflight.sh --image NAME --pool DIR --rpms DIR
#                            --arch NAME [--arch NAME ...]
#
# 时机在镜像组装之前：判据的两半只有这一刻同时在场。固件名字集在闭包池里
# （linux-firmware 是 noarch，四个架构共用一份），模块树的请求名在四个架构
# 的内核模块包里。走过这一步，另外三个架构的模块树就不在宿主上了。
#
# 装机后的复核（T26）只看镜像自己的 rootfs，回答不了「该架构模块树请求的
# 固件，固件树里到底有没有」。riscv64 没有实机兜底，ARM 的板载无线在 QEMU
# 里也不出现，能拦的只有这里。
#
# 取件与判定都在 distro/ci/fw-preflight.sh，本门禁只做三件事：找出两份
# RPM、按架构各调一次、汇总覆盖了哪些架构。调用放在构建容器里，宿主不必
# 装 rpm 与 kmod，工具集与镜像组装用的是同一份。
#
# 退出码：0 请求的架构全部通过；1 有架构缺件或取件失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
# shellcheck source=../lib/log.sh
. "${root}/ci/lib/log.sh"

usage()
{
    printf '%s\n' \
        '用法: ci/gates/fw-preflight.sh --image NAME --pool DIR --rpms DIR' \
        '                              --arch NAME [--arch NAME ...]' \
        '' \
        '  --image  构建容器镜像（提供 rpm、rpm2cpio、cpio、modinfo）' \
        '  --pool   闭包池，内含 linux-firmware 的 RPM' \
        '  --rpms   发行内核包目录，含各架构的 kernel-modules RPM' \
        '  --arch   要预演的架构名，可重复'
}

image=
pool=
rpms=
arches=()
while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --image | --pool | --rpms | --arch) ;;
    *) usage >&2; exit "${EINVAL}" ;;
    esac
    [ "$#" -ge 2 ] || { usage >&2; exit "${EINVAL}"; }
    case "$1" in
    --image) image=$2 ;;
    --pool) pool=$2 ;;
    --rpms) rpms=$2 ;;
    --arch) arches+=("$2") ;;
    esac
    shift 2
done
if [ -z "${image}" ] || [ -z "${pool}" ] || [ -z "${rpms}" ] ||
    [ "${#arches[@]}" -eq 0 ]; then
    usage >&2
    exit "${EINVAL}"
fi

need_cmd docker find
[ -d "${pool}" ] || die "闭包池不存在: ${pool}"
[ -d "${rpms}" ] || die "内核包目录不存在: ${rpms}"
pool=$(cd "${pool}" && pwd -P)
rpms=$(cd "${rpms}" && pwd -P)

# 固件名字集只取一份：全架构共用同一个 noarch 包，按架构各取一次只会把
# 「池里有几份 linux-firmware」变成结论的一部分，而那不是本门禁的问题。
fw_rpm=$(find "${pool}" -type f -name 'linux-firmware-*.rpm' -print |
    sort | head -1)
[ -n "${fw_rpm}" ] || die "闭包池没有 linux-firmware: ${pool}"
say "固件名字集: ${fw_rpm}"

fail=0
cover=()
for arch in "${arches[@]}"; do
    mod_rpm=$(find "${rpms}" -type f \
        -name "airymaxos-kernel-modules-*.${arch}.rpm" -print | head -1)
    if [ -z "${mod_rpm}" ]; then
        printf 'AIRYMAX-FW-PREFLIGHT arch=%s fail=no-rpm rpms=%s\n' \
            "${arch}" "${rpms}"
        fail=1
        continue
    fi
    group "固件预演 ${arch}"
    if docker run --rm \
        -v "${root}:/src:ro" \
        -v "${fw_rpm}:/fw.rpm:ro" \
        -v "${mod_rpm}:/mod.rpm:ro" \
        "${image}" \
        sh /src/distro/ci/fw-preflight.sh --firmware-rpm /fw.rpm \
        --modules-rpm /mod.rpm --arch "${arch}"; then
        cover+=("${arch}")
    else
        fail=1
    fi
    endgroup
done

say "预演覆盖 ${#cover[@]}/${#arches[@]}: ${cover[*]:-无}"
[ "${fail}" -eq 0 ] || die "固件预演未通过，见上方 miss name= 行"
