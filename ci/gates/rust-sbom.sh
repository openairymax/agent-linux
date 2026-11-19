#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/rust-sbom.sh — TUI 工作区的物料清单与依赖准入
#
# 发版要回答两个问题：这颗镜像里有哪些 Rust 依赖，以及它们凭什么被允许。
# 前者是 CycloneDX 清单（AC-S3：组件数与 Cargo.lock 条目一致），后者是
# system/tui/deny.toml（AC-S4：清单随发版资产，纳入同一份来源证明）。
#
# 三个动作各有归属：
#   generate  发版时在宿主跑，产物落进 sha256sum 清单，随摘要清单一起被
#             GPG 签名，并进入 attest 的来源证明，因此「与 SBOM 同 digest」
#             由既有机制判定，不另建一套。
#   verify    流水线里跑：数量、许可证、摘要、字节确定性、无本地路径。
#   policy    只看准入声明本身，不需要 Rust 工具链，放在最便宜的作业。
#
# 为什么清单不写绝对路径：门禁在开发者机器上跑，产物会进公开仓库与发布页。
# cargo metadata 的 id 与 manifest_path 都带本机路径，故只取名字、版本、
# 许可证与锁内摘要；verify 再反向断言产物里没有工作区路径。
#
# 用法:
#   ci/gates/rust-sbom.sh generate --ver VER [--out DIR] [--root DIR]
#   ci/gates/rust-sbom.sh verify [--root DIR]
#   ci/gates/rust-sbom.sh policy [--root DIR]
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly TUI=system/tui
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

CMD=""
ROOT=""
OUT="."
VER=""
TUIDIR=""
MOD=""

usage()
{
    cat <<'EOF'
用法: ci/gates/rust-sbom.sh generate --ver VER [--out DIR] [--root DIR]
      ci/gates/rust-sbom.sh verify [--root DIR]
      ci/gates/rust-sbom.sh policy [--root DIR]
EOF
}

# 清单由 ci/lib/rust_sbom.py 生成与判定；本脚本只负责参数、路径与退出码，
# 判据本身不在这里再写一遍。
py()
{
    python3 "${MOD}" "$@"
}

gen()
{
    local file

    [ -n "${VER}" ] || { err "generate 需要 --ver"; exit "${EINVAL}"; }
    [ -d "${OUT}" ] || die "输出目录不存在: ${OUT}"
    file="${OUT%/}/sbom-agent-linux-${VER}-rust.cdx.json"
    py gen "${TUIDIR}" "${file}" || die "物料清单生成失败"
    note "SBOM: ${file}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        generate|verify|policy)
            [ -z "${CMD}" ] || { err "重复子命令: $1"; exit "${EINVAL}"; }
            CMD="$1"; shift ;;
        --root) need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
        --out) need_dir "$1" "${2:-}"; OUT="$2"; shift 2 ;;
        --ver) VER="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${CMD}" ] || { usage >&2; exit "${EINVAL}"; }
[ -n "${ROOT}" ] ||
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TUIDIR="${ROOT}/${TUI}"
MOD="${ROOT}/ci/lib/rust_sbom.py"
need_dir "--root 下的 TUI 工作区" "${TUIDIR}"
[ -f "${MOD}" ] || die "缺少 ${MOD}"
[ -f "${TUIDIR}/Cargo.lock" ] || die "缺少 ${TUI}/Cargo.lock"
need_cmd python3
python3 -c 'import tomllib' 2>/dev/null ||
    die "python3 缺少 tomllib（需要 3.11 及以上）"

group "rust-sbom: ${CMD}"
case "${CMD}" in
    generate) gen ;;
    verify)
        need_cmd cargo
        py verify "${TUIDIR}" || { endgroup; die "物料清单判定未通过"; } ;;
    policy) py policy "${TUIDIR}" || { endgroup; die "准入声明不满足"; } ;;
esac
endgroup
say "rust-sbom ${CMD} 通过"
