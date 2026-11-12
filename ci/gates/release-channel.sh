#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-channel.sh — 由组装产物生成发行通道清单（V-14-13）
#
# 用法: ci/gates/release-channel.sh --out DIR --version VER
#                                  [--iso PATH] [--iso-oci PATH]
#                                  [--rpm-dir DIR] [--kernel-rpm PATH]
#                                  [--release-rpm PATH] [--arch ARCH]
#                                  [--output FILE] [--released-at STAMP]
#                                  [--notes-url URL]
#
# 清单（channel.json）是版本的对外声明：装了这套系统的人据此判断「有没有
# 新版本、新版本是不是另一个系统」。它只能由产物自身推出来，不能手写——
# 0.0.3 的教训是发布页描述的取件形态与线上制品不一致，而链路上没有任何
# 环节把它判成错。本门禁只负责「找到唯一的那几份产物」，字段取值与交叉
# 断言（ISO 摘要对账本、build 对 release NEVRA）全部在
# tools/release-engineering/release_manifest.py 内完成，不在这里复制一份
# 判定逻辑，避免两处口径分叉。
#
# 产物布局取自 distro/ci/run-image-ci.sh：
#   DIR/desktop/iso/*.iso         ISO 与其 .sha256
#   DIR/desktop/iso/iso-oci.txt   OCI 分块账本（iso-publish 写出）
#   DIR/rpms/RPMS/<arch>/*.rpm    内核包与发行包
#
# 缺账本即判失败：没有账本的 ISO 无法自证「远端取到的是这一份」，此时产出
# 清单等于把无凭据的摘要发出去。命中多份同样判失败——选哪一份都是猜。
#
# 退出码: 0 已产出；1 交叉断言失败；5 参数不满足或产物缺失。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GEN="${ROOT}/tools/release-engineering/release_manifest.py"

OUT=""
ISO_REL="desktop/iso"
VERSION=""
ARCH="x86_64"
ISO=""
OCI=""
RPM_DIR=""
KERNEL=""
RELEASE=""
DEST=""
RELEASED_AT=""
NOTES_URL=""

usage()
{
    cat <<'EOF'
用法: ci/gates/release-channel.sh --out DIR --version VER
                                  [--iso PATH] [--iso-oci PATH]
                                  [--rpm-dir DIR] [--kernel-rpm PATH]
                                  [--release-rpm PATH] [--arch ARCH]
                                  [--output FILE] [--released-at STAMP]
                                  [--notes-url URL]
EOF
}

# pick DIR PATTERN LABEL —— 唯一命中项写入 PICK；零份或多份返回 5。
#
# 递归而不是按固定层级取件：发行包与内核包在本地产物里同层，跨 job 传递
# 后却分属两个制品根（下载保留各自根的相对层级）。按文件名取唯一一份，
# 两种布局都成立，不需要在 workflow 里搬文件。
#
# 结果走全局而不是标准输出：命令替换会另起子 shell，函数里的 exit 只结束
# 那个子 shell，调用方会拿着空串继续往下跑。
PICK=""

pick()
{
    local dir="$1" pattern="$2" label="$3" hits=()

    PICK=""
    [ -d "${dir}" ] || { err "${label} 缺失: 目录不存在 ${dir}"; return "${EINVAL}"; }
    while IFS= read -r hit; do
        hits+=("${hit}")
    done < <(find "${dir}" -type f -name "${pattern}" | sort)
    case "${#hits[@]}" in
        1) PICK="${hits[0]}"; return 0 ;;
        0) err "${label} 缺失: ${dir} 下无 ${pattern}" ;;
        *) err "${label} 应恰好一份，实得 ${#hits[@]} 份: ${pattern}" ;;
    esac
    return "${EINVAL}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)         need_dir "$1" "${2:-}"; OUT="$2"; shift 2 ;;
        --iso-rel)     ISO_REL="${2:-}"; shift 2 ;;
        --version)     VERSION="${2:-}"; shift 2 ;;
        --arch)        ARCH="${2:-}"; shift 2 ;;
        --iso)         ISO="${2:-}"; shift 2 ;;
        --iso-oci)     OCI="${2:-}"; shift 2 ;;
        --rpm-dir)     RPM_DIR="${2:-}"; shift 2 ;;
        --kernel-rpm)  KERNEL="${2:-}"; shift 2 ;;
        --release-rpm) RELEASE="${2:-}"; shift 2 ;;
        --output)      DEST="${2:-}"; shift 2 ;;
        --released-at) RELEASED_AT="${2:-}"; shift 2 ;;
        --notes-url)   NOTES_URL="${2:-}"; shift 2 ;;
        -h|--help)     usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${OUT}" ] && [ -n "${VERSION}" ] || { usage >&2; exit "${EINVAL}"; }
[ -f "${GEN}" ] || { err "缺少生成器: ${GEN}"; exit "${EINVAL}"; }
need_cmd python3 find

[ -n "${RPM_DIR}" ] || RPM_DIR="${OUT}/rpms/RPMS/${ARCH}"
need_dir "--rpm-dir" "${RPM_DIR}"

if [ -z "${ISO}" ]; then
    pick "${OUT}/${ISO_REL}" '*.iso' "ISO" || exit "${EINVAL}"
    ISO="${PICK}"
fi
[ -n "${OCI}" ] || OCI="${OUT}/${ISO_REL}/iso-oci.txt"
[ -f "${OCI}" ] || { err "缺少 OCI 分块账本: ${OCI}"; exit "${EINVAL}"; }
if [ -z "${KERNEL}" ]; then
    pick "${RPM_DIR}" 'airymaxos-kernel-[0-9]*.rpm' "内核 RPM" ||
        exit "${EINVAL}"
    KERNEL="${PICK}"
fi
if [ -z "${RELEASE}" ]; then
    pick "${RPM_DIR}" 'airymaxos-release-*.rpm' "发行 RPM" || exit "${EINVAL}"
    RELEASE="${PICK}"
fi
[ -n "${DEST}" ] || DEST="${OUT}/channel.json"

args=(--channel --version "${VERSION}" --iso "${ISO}" --iso-oci "${OCI}"
      --kernel-rpm "${KERNEL}" --release-rpm "${RELEASE}" --output "${DEST}")
[ -z "${RELEASED_AT}" ] || args+=(--released-at "${RELEASED_AT}")
[ -z "${NOTES_URL}" ] || args+=(--notes-url "${NOTES_URL}")

python3 "${GEN}" "${args[@]}"
rc=$?
case "${rc}" in
0) ;;
1) err "通道清单交叉断言失败，未产出: ${DEST}"; exit 1 ;;
*) err "生成器参数/环境错误（rc=${rc}）"; exit "${EINVAL}" ;;
esac

[ -s "${DEST}" ] || { err "生成器返回 0 但清单为空: ${DEST}"; exit 1; }
say "通道清单: ${DEST}"
