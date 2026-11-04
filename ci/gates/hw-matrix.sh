#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/hw-matrix.sh — 五类硬件能力矩阵与按能力选机（V-10 / HW-01、HW-02）
#
# 用法:
#   ci/gates/hw-matrix.sh [--root DIR] [--class LIST] [--require LIST] [--json]
#
# 背景：0.0.3 的目标机登记靠机型名，换一台同代但配置不同的机器就要改一次
# 代码；反过来，机型对得上但无线网卡没被驱动接管的机器又能混进验收。机型
# 名与 DMI 只描述厂商命名，不描述这台机器当下有没有可用显示、无线、音频、
# 输入与背光。本门禁把判定改成五类能力节点，结论只有 pass / n/a / fail：
# n/a 必须带 note 说明是本机无此硬件还是缺内核能力（并写明顺延版本），
# 不允许因为没有证据就整类不打印。
#
# 选机由 --require 驱动：列出的类必须全部 pass，任一 n/a 或 fail 即拒绝，
# 并给出缺失类。判定链只读 sysfs/procfs，没有 DMI、机型名或品牌白名单，
# 同一条命令在 x86_64 / aarch64 / riscv64 上语义一致。
#
# --root 指向一棵抓取下来的证据树（sys/、proc/），本地与历史产物都能复算；
# 缺省为 /，直接在目标机上取证。--json 供流水线消费，人读形态每类一行。
#
# 退出码: 0 无 fail 且选机通过；1 有 fail 或选机拒绝；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK="ci/lib/hwcap.py"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
ONLY=""
NEED=""
JSON=0

usage()
{
    cat <<'EOF'
用法: ci/gates/hw-matrix.sh [--root DIR] [--class LIST] [--require LIST] [--json]
  --root DIR      证据树根（缺省 /）
  --class LIST    只判定列出的类（逗号分隔）
  --require LIST  选机所需能力类，全部 pass 才接受
  --json          输出 JSON
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)
            [ -n "${2:-}" ] || { err "--root 需要目录路径"; exit "${EINVAL}"; }
            TREE="$2"
            shift 2
            ;;
        --class)
            [ -n "${2:-}" ] || { err "--class 需要能力类列表"; exit "${EINVAL}"; }
            ONLY="$2"
            shift 2
            ;;
        --require)
            [ -n "${2:-}" ] || { err "--require 需要能力类列表"; exit "${EINVAL}"; }
            NEED="$2"
            shift 2
            ;;
        --json) JSON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

args=()
[ -z "${TREE}" ] || args+=(--root "${TREE}")
[ -z "${ONLY}" ] || args+=(--class "${ONLY}")
[ -z "${NEED}" ] || args+=(--require "${NEED}")
[ "${JSON}" -eq 0 ] || args+=(--json)

exec python3 "${ROOT}/${CHECK}" "${args[@]}"
