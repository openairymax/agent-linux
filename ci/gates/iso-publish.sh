#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/iso-publish.sh — 把 ISO 交付到 OCI 制品通道
#
# 用法: ci/gates/iso-publish.sh --out DIR --tag v0.0.3
#                               [--ref ghcr.io/openairymax/airymaxos-iso]
#                               [--iso-rel desktop/iso] [--force]
#
# 为什么走 OCI：ISO 3.43 GiB，GitHub Release 单资产上限 2 GiB，AtomGit 网关
# 3 GiB 直接 413。两条 Release 通道都放不下，只有 registry 的 blob 通道能
# 承载「原文件字节」，且 layer digest 就是文件 sha256。
#
# 为什么单独一个包：release.yml 已经在 ghcr.io/openairymax/airymaxos 下按
# tag 推 OCI 基础镜像。同一个 tag 再放 ISO 清单，`oras pull` 与 `docker
# pull` 都会按 tag 解析，谁后写谁赢；签名也按 digest 附着，排查时无法判断
# 拿到的是镜像还是 ISO。故 ISO 走独立包 airymaxos-iso，tag 与发行 tag 一一
# 对应，两个通道互不覆盖。
#
# 幂等与不可变：同 tag 已存在时先比对 layer 摘要——相同即视为已发布，不重
# 传 3.4 GiB；不同即失败，发行产物被静默替换比没有发布更危险。确需覆盖
# 才用 --force。
#
# 断言：layer digest 必须等于本地 ISO 的 sha256。registry 接收 blob 时按
# 内容算摘要，因此这一条同时证明传输无损、远端就是本地产物。
#
# 退出码: 0 已发布或已存在；1 推送/断言失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ARTIFACT_TYPE="application/vnd.airymaxos.iso.v1"
readonly LAYER_TYPE="application/vnd.airymaxos.iso.layer.v1+iso"
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/host.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/host.sh"

OUT=""
TAG=""
REF="ghcr.io/openairymax/airymaxos-iso"
ISO_REL="desktop/iso"
FORCE=0

usage()
{
    cat <<'EOF'
用法: ci/gates/iso-publish.sh --out DIR --tag v0.0.3
                              [--ref ghcr.io/openairymax/airymaxos-iso]
                              [--iso-rel desktop/iso] [--force]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)     OUT="${2:-}"; shift 2 ;;
        --tag)     TAG="${2:-}"; shift 2 ;;
        --ref)     REF="${2:-}"; shift 2 ;;
        --iso-rel) ISO_REL="${2:-}"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${OUT}" ] || { usage >&2; exit "${EINVAL}"; }

# 未指定 tag 时不是错误：image-build 的日常手动轮次只做组装与验收，不对外发布。
# 由调用方决定是否带 tag，门禁不替它猜。
if [ -z "${TAG}" ]; then
    note "未指定 --tag，跳过 OCI 发布"
    exit 0
fi

need_cmd oras python3 sha256sum stat numfmt

iso="$(find "${OUT}/${ISO_REL}" -maxdepth 1 -name '*.iso' -type f | head -n 1)"
[ -n "${iso}" ] || die "缺少 ISO: ${OUT}/${ISO_REL}"
name="$(basename "${iso}")"
sum_file="${iso}.sha256"
[ -f "${sum_file}" ] || die "缺少 ISO 校验和: ${sum_file}"

sum="$(sha256sum "${iso}" | cut -d' ' -f1)"
# 先与本地产出的 .sha256 对齐，再谈上传：两份摘要都错的文件没有传播价值。
( cd "$(dirname "${iso}")" && sha256sum -c "${name}.sha256" >/dev/null ) ||
    die "ISO 摘要与其 .sha256 不符: ${name}"
size="$(stat -c %s "${iso}")"
say "ISO ${name}: sha256 ${sum}, $(numfmt --to=iec --suffix=B "${size}")"

# GHCR 凭据由调用方以环境变量注入（GitHub Actions 用 GITHUB_TOKEN）：
# 未注入时假定当前机器已 oras login，本地重放不需要再造一套凭据。
if [ -n "${GHCR_TOKEN:-}" ]; then
    printf '%s' "${GHCR_TOKEN}" |
        oras login "${REF%%/*}" \
            --username "${GHCR_USER:-${GITHUB_ACTOR:-token}}" \
            --password-stdin >/dev/null ||
        die "oras login ${REF%%/*} 失败"
fi

target="${REF}:${TAG}"

# 远端 layer 摘要；清单不存在或无法解析时输出空串（由调用方区分两种情形）。
remote_layer()
{
    oras manifest fetch "${target}" 2>/dev/null |
        python3 -c 'import json,sys
try:
    m = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
print(m["layers"][0]["digest"] if m.get("layers") else "")'
}

want="sha256:${sum}"
have="$(remote_layer)"

if [ -n "${have}" ] && [ "${have}" = "${want}" ] && [ "${FORCE}" -eq 0 ]; then
    say "${target} 已指向 ${want}，跳过上传"
elif [ -n "${have}" ] && [ "${FORCE}" -eq 0 ]; then
    die "${target} 已存在且指向 ${have}，与本地 ${want} 不同；确认要替换才加 --force"
else
    group "oras push ${target}"
    ( cd "$(dirname "${iso}")" &&
        oras push --artifact-type "${ARTIFACT_TYPE}" \
            "${target}" "${name}:${LAYER_TYPE}" ) || die "oras push 失败: ${target}"
    endgroup
fi

have="$(remote_layer)"
[ "${have}" = "${want}" ] || die "远端 layer ${have:-<空>} 与本地 ${want} 不一致"

ref_file="$(dirname "${iso}")/iso-oci.txt"
cat >"${ref_file}" <<EOF
ref=${REF}
tag=${TAG}
sha256=${sum}
size=${size}
layer=${have}
pull=oras pull ${target} -o .
EOF

say "ISO 已发布: ${target}"
say "拉取: oras pull ${target} -o ."
say "引用记录: ${ref_file}"
