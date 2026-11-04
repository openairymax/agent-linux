#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-sign.sh — 发布物料的 GPG 能力自检与分离签名
#
# 用法: ci/ci.sh release-sign probe
#       ci/ci.sh release-sign key       --ver VER
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
# key 导出发行公钥：签名页面上每份 .asc 的可验证性，取决于取件方手里有没有
# 对应公钥。私钥只活在 runner 的临时密钥环里（job 结束即随 runner 销毁），
# 公钥若不随物料发布，签名就只剩「有值」而没有「可验」——发布页给了 .asc
# 却不给公钥，等于让取件方自己猜密钥。
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
      ci/ci.sh release-sign key       --ver VER
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

# key —— 导出「实际签名的那把私钥」对应的公钥。
#
# 不按 UID 或邮箱取密钥：密钥环里可能同时存在旧密钥与备用密钥，按名字取会
# 导出一把本次没签过任何物料的公钥，取件方拿它验签全部失败，而发布流程全程
# 无报错。签名用的是哪把私钥，公钥就导出哪把——指纹从密钥环的 sec 记录读，
# 与签名用的是同一个 keyring，不引入第二处事实源。
key()
{
    local ver="$1" fpr out

    need_cmd gpg
    fpr="$(gpg --batch --list-secret-keys --with-colons 2>/dev/null |
        awk -F: '$1 == "sec" { print $5; exit }')"
    [ -n "${fpr}" ] || die "密钥环里没有私钥，无法导出发行公钥"
    out="agent-linux-${ver}-gpg-key.asc"
    gpg --batch --yes --armor --export "${fpr}" >"${out}" ||
        die "导出公钥失败: ${fpr:0:16}"
    [ -s "${out}" ] || die "导出的公钥为空: ${out}"
    say "已导出发行公钥: ${out}（${fpr:0:16}…）"
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
            "agent-linux-${ver}-gpg-key.asc" \
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
    key)
        [ -n "${VER}" ] || { err "缺少 --ver"; exit "${EINVAL}"; }
        key "${VER}"
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
