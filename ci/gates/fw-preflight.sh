#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/fw-preflight.sh — 按架构预演固件扁平入口
#
# 用法: ci/gates/fw-preflight.sh --image NAME --pool DIR --rpms DIR
#                                --dnf-cache DIR --arch NAME [--arch NAME ...]
#
# 时机在镜像组装之前：判据的两半只有这一刻同时在场。固件名字集在闭包池里
# （固件包都是 noarch，四个架构共用一份），模块树的请求名在四个架构的内核
# 模块包里。走过这一步，另外三个架构的模块树就不在宿主上了。
#
# 固件名字集按 distro/firmware/fw-manifest.txt 取，而不是写死 linux-firmware：
# 镜像里的固件树是清单里所有提供包的并集，漏一个包就会把它提供的条目报成
# 缺件（run 42797570216：regulatory.db 与 regulatory.db.p7s 由 wireless-regdb
# 提供，却因为只读了 linux-firmware 而判负）。
#
# 不支持类器件走 distro/firmware/fw-exempt.txt 显式记账：表内命中打 exempt
# 行、不计入 miss，表外缺件仍判负。判据不缩到某几个驱动，否则新驱动带来的
# 缺件会静悄悄通过。
#
# 装机后的复核（T26）只看镜像自己的 rootfs，回答不了「该架构模块树请求的
# 固件，固件树里到底有没有」。riscv64 没有实机兜底，ARM 的板载无线在 QEMU
# 里也不出现，能拦的只有这里。
#
# 取件与判定都在 distro/ci/fw-preflight.sh，本门禁只做三件事：找出两份
# RPM、按架构各调一次、汇总覆盖了哪些架构。
#
# 工具链走 --dnf-cache 离线补齐，和镜像组装同一份缓存：基础镜像自带 rpm，
# 但不带 cpio、kmod 与 findutils，直接在裸镜像里跑会把「工具」误报成
# 「包坏」——run 42774563541 的现场是四个架构全报 fail=no-modules，实际是
# cpio 不在；run 42790132617 则是 cpio 在而 find 不在，模块树解出来却是零
# 文件（命令被 2>/dev/null 吞掉，退出码也不报错）。四个架构共用一次 docker
# 会话，缓存只需解一次；这层补齐也把「工具缺」与「模块包缺内容」分成两条
# 独立记号（no-toolchain / no-modules），不再让人对着一个笼统的 fail= 去猜。
# 装完逐个探一遍，缺哪个就直接点名，不再让下游把它读成包的问题。
#
# 退出码：0 请求的架构全部通过；1 有架构缺件或取件失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

IMAGE=""
POOL=""
RPMS=""
DNF_CACHE=""
ARCHES=()

usage()
{
    printf '%s\n' \
        '用法: ci/gates/fw-preflight.sh --image NAME --pool DIR --rpms DIR' \
        '                              --dnf-cache DIR --arch NAME [--arch NAME ...]' \
        '' \
        '  --image      构建容器镜像（提供 rpm、dnf）' \
        '  --pool       闭包池，内含固件清单声明的全部固件包' \
        '  --rpms       发行内核包目录，含各架构的 kernel-modules RPM' \
        '  --dnf-cache  工具链缓存，容器内据此离线补 cpio、kmod 与 findutils' \
        '  --arch       要预演的架构名，可重复'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --image|--pool|--rpms|--dnf-cache|--arch) ;;
        *) usage >&2; exit "${EINVAL}" ;;
    esac
    # 取值前先确认还有下一个实参：`shift 2` 在只剩一个参数时不会移位，
    # 直接落进下一轮就成了死循环，缺值必须就地按用法错误处理。
    [ "$#" -ge 2 ] || { usage >&2; exit "${EINVAL}"; }
    case "$1" in
        --image)     IMAGE="$2" ;;
        --pool)      POOL="$2" ;;
        --rpms)      RPMS="$2" ;;
        --dnf-cache) DNF_CACHE="$2" ;;
        --arch)      ARCHES+=("$2") ;;
    esac
    shift 2
done

if [ -z "${IMAGE}" ] || [ -z "${POOL}" ] || [ -z "${RPMS}" ] ||
    [ -z "${DNF_CACHE}" ] || [ "${#ARCHES[@]}" -eq 0 ]; then
    usage >&2
    exit "${EINVAL}"
fi

need_cmd docker find
[ -d "${POOL}" ] || die "闭包池不存在: ${POOL}"
[ -d "${RPMS}" ] || die "内核包目录不存在: ${RPMS}"
[ -d "${DNF_CACHE}" ] ||
    die "工具链缓存不存在，先跑 ci/ci.sh toolchain: ${DNF_CACHE}"
POOL="$(cd "${POOL}" && pwd -P)"
RPMS="$(cd "${RPMS}" && pwd -P)"
DNF_CACHE="$(cd "${DNF_CACHE}" && pwd -P)"

# 固件名字集取清单声明的每个提供包：镜像是这些包的并集，只读其中一个就会
# 把另一个包的条目报成缺件。清单里同一包按文件重复出现，按包名去重。
MANIFEST="${ROOT}/distro/firmware/fw-manifest.txt"
EXEMPT="${ROOT}/distro/firmware/fw-exempt.txt"
[ -r "${MANIFEST}" ] || die "固件清单不存在: ${MANIFEST}"
[ -r "${EXEMPT}" ] || die "固件豁免表不存在: ${EXEMPT}"
FW_RPMS=""
fw_seen=" "
while read -r fw_pkg _; do
    case "${fw_pkg}" in
        ''|'#'*) continue ;;
    esac
    case "${fw_seen}" in
        *" ${fw_pkg} "*) continue ;;
    esac
    fw_seen="${fw_seen}${fw_pkg} "
    fw_rpm="$(find "${POOL}" -type f -name "${fw_pkg}-*.rpm" -print |
        sort | head -1)"
    [ -n "${fw_rpm}" ] || die "闭包池没有 ${fw_pkg}: ${POOL}"
    FW_RPMS="${FW_RPMS} --firmware-rpm /pool/$(basename "${fw_rpm}")"
    say "固件名字集: $(basename "${fw_rpm}")"
done <"${MANIFEST}"
[ -n "${FW_RPMS}" ] || die "固件清单没有声明提供包: ${MANIFEST}"

# 模块包在宿主上按架构定位，容器里只认路径：容器内没有 find 也照样能跑，
# 且「哪个架构没有包」由宿主报，指向的是实际挂进来的目录。找不到的架构
# 仍占一行（路径为空），让容器统一打印 fail=no-rpm，不在这里提前退出——
# 缺一个架构不该掩盖其余三个的判定结果。路径换成容器内的视角：宿主绝对
# 路径在容器里不存在，照抄过去只会让每个架构都报取件失败。
mods=()
for arch in "${ARCHES[@]}"; do
    mod="$(find "${RPMS}" -type f \
        -name "airymaxos-kernel-modules-*.${arch}.rpm" -print | sort | head -1)"
    [ -z "${mod}" ] || mod="/rpms${mod#"${RPMS}"}"
    mods+=("${arch}:${mod}")
done
MODS="$(printf '%s\n' "${mods[@]}")"

# 取件与判定都在容器里：宿主不必有 rpm、cpio、kmod。dnf 以 --cacheonly
# 从挂进来的缓存装工具，repo 定义随缓存一起挂——缺了仓库表就只剩「没有
# 可用仓库」，缓存里的 cpio/kmod 反而取不到。
read -r -d '' ctr_sh <<'EOF'
set -u
dnf -y install --cacheonly --setopt=keepcache=1 cpio kmod findutils \
    >/dev/null || {
    printf 'AIRYMAX-FW-PREFLIGHT fail=no-toolchain need="cpio kmod findutils"\n'
    exit 1
}
for tool in find cpio rpm2cpio; do
    command -v "${tool}" >/dev/null 2>&1 && continue
    printf 'AIRYMAX-FW-PREFLIGHT fail=no-toolchain need="%s"\n' "${tool}"
    exit 1
done
fail=0
for entry in ${MODS}; do
    arch=${entry%%:*}
    mod=${entry#*:}
    if [ -z "${mod}" ] || [ ! -f "${mod}" ]; then
        printf 'AIRYMAX-FW-PREFLIGHT arch=%s fail=no-rpm path=%s\n' \
            "${arch}" "${mod:-/rpms}"
        fail=1
        continue
    fi
    sh /src/distro/ci/fw-preflight.sh ${FW_RPMS} \
        --modules-rpm "${mod}" --arch "${arch}" \
        --exempt /src/distro/firmware/fw-exempt.txt || fail=1
done
exit "${fail}"
EOF

# -v 的实参是单个 src:dst[:opts] 字符串，拆成多元素会被当成镜像名。
mounts=(-v "${ROOT}:/src:ro" -v "${POOL}:/pool:ro" -v "${RPMS}:/rpms:ro"
        -v "${DNF_CACHE}:/var/cache/dnf:rw")
mounts+=(-v "${ROOT}/distro/repo/airymax-os.repo:/etc/yum.repos.d/99-airy-upstream.repo:ro")

log="$(mktemp)"
trap 'rm -f "${log}"' EXIT INT TERM

group "固件预演 ${ARCHES[*]}"
docker run --rm "${mounts[@]}" \
    -e MODS="${MODS}" \
    -e FW_RPMS="${FW_RPMS}" \
    "${IMAGE}" sh -c "${ctr_sh}" 2>&1 | tee "${log}"
rc=${PIPESTATUS[0]}
endgroup

cover=$(grep -cE '^AIRYMAX-FW-PREFLIGHT arch=[^ ]+ flat=[0-9]+ miss=0$' "${log}")
say "预演覆盖 ${cover}/${#ARCHES[@]}"
[ "${rc}" -eq 0 ] || die "固件预演未通过，见上方 fail= 行"
