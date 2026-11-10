#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/fw-preflight.sh — 按架构预演固件扁平入口
#
# 用法: ci/gates/fw-preflight.sh --image NAME --pool DIR --rpms DIR
#                                --dnf-cache DIR --arch NAME [--arch NAME ...]
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
# RPM、按架构各调一次、汇总覆盖了哪些架构。
#
# 工具链走 --dnf-cache 离线补齐，和镜像组装同一份缓存：基础镜像自带 rpm，
# 但不带 cpio 与 kmod，直接在裸镜像里跑会把「工具」误报成「包坏」——run
# 42774563541 的现场是四个架构全报 fail=no-modules，实际是 cpio 不在。
# 四个架构共用一次 docker 会话，缓存只需解一次；这层补齐也把「工具缺」
# 与「模块包缺内容」分成两条独立记号（no-toolchain / no-modules），不再
# 让人对着一个笼统的 fail= 去猜。
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
        '  --pool       闭包池，内含 linux-firmware 的 RPM' \
        '  --rpms       发行内核包目录，含各架构的 kernel-modules RPM' \
        '  --dnf-cache  工具链缓存，容器内据此离线补 cpio 与 kmod' \
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

# 固件名字集只取一份：全架构共用同一个 noarch 包，按架构各取一次只会把
# 「池里有几份 linux-firmware」变成结论的一部分，而那不是本门禁的问题。
FW_RPM="$(find "${POOL}" -type f -name 'linux-firmware-*.rpm' -print |
    sort | head -1)"
[ -n "${FW_RPM}" ] || die "闭包池没有 linux-firmware: ${POOL}"
say "固件名字集: $(basename "${FW_RPM}")"

# 模块包在宿主上按架构定位，容器里只认路径：容器内没有 find 也照样能跑，
# 且「哪个架构没有包」由宿主报，指向的是实际挂进来的目录。找不到的架构
# 仍占一行（路径为空），让容器统一打印 fail=no-rpm，不在这里提前退出——
# 缺一个架构不该掩盖其余三个的判定结果。
mods=()
for arch in "${ARCHES[@]}"; do
    mod="$(find "${RPMS}" -type f \
        -name "airymaxos-kernel-modules-*.${arch}.rpm" -print | sort | head -1)"
    mods+=("${arch}:${mod}")
done
MODS="$(printf '%s\n' "${mods[@]}")"

# 取件与判定都在容器里：宿主不必有 rpm、cpio、kmod。dnf 以 --cacheonly
# 从挂进来的缓存装工具，repo 定义随缓存一起挂——缺了仓库表就只剩「没有
# 可用仓库」，缓存里的 cpio/kmod 反而取不到。
read -r -d '' ctr_sh <<'EOF'
set -u
dnf -y install --cacheonly --setopt=keepcache=1 cpio kmod >/dev/null || {
    printf 'AIRYMAX-FW-PREFLIGHT fail=no-toolchain need="cpio kmod"\n'
    exit 1
}
fail=0
for entry in ${MODS}; do
    arch=${entry%%:*}
    mod=${entry#*:}
    if [ -z "${mod}" ]; then
        printf 'AIRYMAX-FW-PREFLIGHT arch=%s fail=no-rpm rpms=/rpms\n' "${arch}"
        fail=1
        continue
    fi
    sh /src/distro/ci/fw-preflight.sh --firmware-rpm "${FW_RPM}" \
        --modules-rpm "${mod}" --arch "${arch}" || fail=1
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
    -e FW_RPM="/pool/$(basename "${FW_RPM}")" \
    "${IMAGE}" sh -c "${ctr_sh}" 2>&1 | tee "${log}"
rc=${PIPESTATUS[0]}
endgroup

cover=$(grep -cE '^AIRYMAX-FW-PREFLIGHT arch=[^ ]+ flat=[0-9]+ miss=0$' "${log}")
say "预演覆盖 ${cover}/${#ARCHES[@]}"
[ "${rc}" -eq 0 ] || die "固件预演未通过，见上方 fail= 行"
