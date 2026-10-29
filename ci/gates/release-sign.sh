#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-sign.sh — 发布物料的 GPG 能力自检与分离签名
#
# 用法: ci/ci.sh release-sign probe
#       ci/ci.sh release-sign artifacts --ver VER
#       ci/ci.sh release-sign manifest  --ver VER --tag TAG
#
# 私钥缺席或口令错误是「几秒可证伪、却只在发布末尾才暴露」的输入：旧流程
# 要跑满四架构内核与 SDK 才在签名步失败，整轮作废。probe 在任何长步骤之前
# 做一次真实签名，artifacts 签 RPM/SDK/pyz，manifest 生成校验和与 release
# notes 后再签。
#
# 口令只经环境变量 GPG_PASSPHRASE 传入。--passphrase-fd 3 读取的是 here-string，
# 不会出现在进程参数里（ps 可见）。
#
# 退出码: 0 通过；1 签名或物料失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/ci.sh release-sign probe
      ci/ci.sh release-sign artifacts --ver VER
      ci/ci.sh release-sign manifest  --ver VER --tag TAG
EOF
}

sign()
{
    local f="$1"

    [ -f "${f}" ] || die "缺少待签文件: ${f}"
    gpg --batch --yes --pinentry-mode loopback --passphrase-fd 3 \
        --detach-sign --armor -o "${f}.asc" "${f}" 3<<<"${GPG_PASSPHRASE:-}" ||
        die "签名失败: ${f}"
}

probe()
{
    local dir f

    need_cmd gpg
    dir="$(mktemp -d)"
    f="${dir}/probe"
    printf 'airymaxos signing probe\n' > "${f}"
    sign "${f}"
    gpg --verify "${f}.asc" "${f}" || die "签名自检失败：私钥不可用"
    rm -rf "${dir}"
    say "签名能力自检通过"
}

artifacts()
{
    local ver="$1" f files=()

    need_cmd gpg
    mapfile -t files < <(find dist/rpm -name '*.rpm' | sort)
    files+=("agent-linux-sdk-${ver}.tar.gz" "airy-flash-${ver}.pyz")
    for f in "${files[@]}"; do
        sign "${f}"
    done
    say "已签名 ${#files[@]} 份物料"
}

manifest()
{
    local ver="$1" tag="$2" assets=()

    need_cmd gpg python3
    mapfile -t assets < <(
        find dist/rpm -type f \( -name '*.rpm' -o -name '*.rpm.asc' \) | sort
        printf '%s\n' \
            "sbom-agent-linux-${ver}.spdx.json" \
            "agent-linux-sdk-${ver}.tar.gz" \
            "agent-linux-sdk-${ver}.tar.gz.asc" \
            "airy-flash-${ver}.pyz" \
            "airy-flash-${ver}.pyz.asc" \
            "oci-image-${tag}.tar"
    )
    python3 tools/release-engineering/release_manifest.py --checksums \
        --output "agent-linux-${ver}.sha256sum" "${assets[@]}" ||
        die "生成摘要清单失败"
    python3 tools/release-engineering/release_manifest.py --notes \
        --version "${ver}" \
        --output "agent-linux-${ver}-release-notes.md" ||
        die "生成 release notes 失败"
    for f in "agent-linux-${ver}.sha256sum" \
             "agent-linux-${ver}-release-notes.md"; do
        sign "${f}"
    done
    ls -l "agent-linux-${ver}".sha256sum* "agent-linux-${ver}"-release-notes.md*
}

VER=""
TAG=""
cmd="${1:-}"
[ "$#" -gt 0 ] || { usage; exit "${EINVAL}"; }
shift

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ver)     VER="${2:-}"; shift 2 ;;
        --tag)     TAG="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

case "${cmd}" in
    probe)
        probe
        ;;
    artifacts)
        [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
        artifacts "${VER}"
        ;;
    manifest)
        [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
        [ -n "${TAG}" ] || { err "缺少 --tag"; exit "${EINVAL}"; }
        manifest "${VER}" "${TAG}"
        ;;
    -h|--help)
        usage
        ;;
    *)
        usage >&2
        exit "${EINVAL}"
        ;;
esac
