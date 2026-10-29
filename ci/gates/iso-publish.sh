#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/iso-publish.sh — 把 ISO 切成定长 layer 交付到 OCI 制品通道
#
# 用法: ci/gates/iso-publish.sh --out DIR --tag v0.0.3
#                               [--ref ghcr.io/openairymax/airymaxos-iso]
#                               [--iso-rel desktop/iso]
#                               [--chunk-size BYTES] [--force]
#
# 为什么走 OCI：ISO 3.4 GiB，GitHub Release 单资产上限 2 GiB，AtomGit 网关
# 3 GiB 直接 413。两条 Release 通道都放不下，只有 registry 的 blob 通道能
# 承载「原文件字节」，且 layer digest 由接收端按内容计算，等于传输无损的
# 第三方证明。
#
# 为什么分块：0.0.3 实测，镜像站对接 registry 的 Range 支持并不一致——
# 有的站点忽略 Range 恒返 200 全量，单连接拉到 314572933 B 就断流且无法续
# 传；直连 ghcr.io 能 206 但只有 34 KB/s。把 ISO 切成 192 MiB 的 layer 后，
# 取件侧可并发、可续传、坏块只需重取一块，单块 2.4 MB/s 下约 80 s，失败
# 代价从「重拉 3.4 GiB」降到「重拉一块」。
#
# 块边界是产物的一部分：manifest annotation 记录整体 sha256、块大小与块
# 数，取件侧据此确认「远端就是本地这一份切法」。同 sha256 + 同块大小 +
# 同块数即可推出块字节完全相同，故幂等判定只读 annotation，不重切 3.4 GiB。
#
# 为什么单独一个包：release.yml 已经在 ghcr.io/openairymax/airymaxos 下按
# tag 推 OCI 基础镜像。同一个 tag 再放 ISO 清单，`oras pull` 与 `docker
# pull` 都会按 tag 解析，谁后写谁赢；签名也按 digest 附着，排查时无法判断
# 拿到的是镜像还是 ISO。故 ISO 走独立包 airymaxos-iso，tag 与发行 tag 一一
# 对应，两个通道互不覆盖。
#
# 幂等与不可变：同 tag 已存在且三元组（sha256/块大小/块数）相同即视为已发
# 布，不重传；不同即失败，发行产物被静默替换比没有发布更危险。确需覆盖才
# 用 --force。
#
# 断言：推送后重新拉取 manifest，逐块比对 digest（顺序敏感）、整体 sha256
# 与 annotation 三元组。块 digest 一致即证明每块字节一致，按序拼接即为
# 本地 ISO。
#
# 磁盘：分块在 $TMPDIR 下进行，峰值需额外一份 ISO 大小的空间，由调用方
# （free-disk 门禁）保证；不以 $OUT 为暂存区，避免分块混进交付产物。
#
# 退出码: 0 已发布或已存在；1 推送/断言失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ARTIFACT_TYPE="application/vnd.airymaxos.iso.v1"
readonly LAYER_TYPE="application/vnd.airymaxos.iso.layer.v1+iso"
readonly DEFAULT_CHUNK=201326592
readonly MIN_CHUNK=1048576
readonly ANN="org.openairymax.iso"
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

OUT=""
TAG=""
REF="ghcr.io/openairymax/airymaxos-iso"
ISO_REL="desktop/iso"
CHUNK="${DEFAULT_CHUNK}"
FORCE=0
WORK=""

usage()
{
    cat <<'EOF'
用法: ci/gates/iso-publish.sh --out DIR --tag v0.0.3
                              [--ref ghcr.io/openairymax/airymaxos-iso]
                              [--iso-rel desktop/iso]
                              [--chunk-size BYTES] [--force]
EOF
}

cleanup()
{
    [ -n "${WORK}" ] && rm -rf "${WORK}"
    return 0
}

# 远端 manifest 存在时落盘到 ${WORK}/remote.json；不存在或不可解析返回 1。
fetch_remote()
{
    oras manifest fetch "${target}" >"${WORK}/remote.json" 2>/dev/null || return 1
    [ -s "${WORK}/remote.json" ] || return 1
    return 0
}

# 读远端 annotation（$1 为 ${ANN} 后的后缀），缺失输出空串。
remote_field()
{
    ISO_ANN="${ANN}" python3 -c '
import json, os, sys
try:
    m = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(0)
print((m.get("annotations") or {}).get(os.environ["ISO_ANN"] + "." + sys.argv[2], ""))
' "${WORK}/remote.json" "$1"
}

# 逐行输出远端 layer 的 "digest<TAB>size"，顺序即清单顺序。
remote_parts()
{
    python3 -c '
import json, sys
try:
    m = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(0)
for l in m.get("layers") or []:
    print(l.get("digest", "") + "\t" + str(l.get("size", 0)))
' "${WORK}/remote.json"
}

# 从远端清单还原 parts.txt，供「已发布」路径直接写引用记录。
parts_from_remote()
{
    remote_parts |
        awk -v n="${name}" '
            { d = $1; sub(/^sha256:/, "", d)
              printf "%04d %s %s %s.part%04d\n", NR - 1, d, $2, n, NR - 1 }' \
            >"${WORK}/parts.txt"
}

# 从本地分块生成 parts.txt，并就块数与总字节对账。
parts_from_files()
{
    local p i=0 pd ps total=0

    : >"${WORK}/parts.txt"
    for p in "${WORK}/${name}.part"*; do
        [ -f "${p}" ] || die "缺少分块: ${p}"
        pd="$(sha256sum "${p}" | cut -d' ' -f1)"
        ps="$(stat -c %s "${p}")"
        [ "${ps}" -gt 0 ] || die "空分块: ${p}"
        printf '%04d %s %s %s\n' "${i}" "${pd}" "${ps}" "$(basename "${p}")" \
            >>"${WORK}/parts.txt"
        total=$((total + ps))
        i=$((i + 1))
    done
    [ "${i}" -eq "${count}" ] || die "分块数 ${i} 与预期 ${count} 不符"
    [ "${total}" -eq "${size}" ] || die "分块总大小 ${total} 与 ISO ${size} 不符"
}

# 推送后复核：annotation 三元组 + 逐块 digest（顺序敏感）。
verify_remote()
{
    [ "$(remote_field sha256)" = "${sum}" ] ||
        die "远端 ${ANN}.sha256 与本地 ${sum} 不一致"
    [ "$(remote_field chunk-size)" = "${CHUNK}" ] ||
        die "远端 ${ANN}.chunk-size 与本地 ${CHUNK} 不一致"
    [ "$(remote_field chunks)" = "${count}" ] ||
        die "远端 ${ANN}.chunks 与本地 ${count} 不一致"
    diff -u <(awk '{print "sha256:" $2}' "${WORK}/parts.txt") \
        <(remote_parts | cut -f1) >&2 ||
        die "远端 layer 与本地分块不一致: ${target}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)        OUT="${2:-}"; shift 2 ;;
        --tag)        TAG="${2:-}"; shift 2 ;;
        --ref)        REF="${2:-}"; shift 2 ;;
        --iso-rel)    ISO_REL="${2:-}"; shift 2 ;;
        --chunk-size) CHUNK="${2:-}"; shift 2 ;;
        --force)      FORCE=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${OUT}" ] || { usage >&2; exit "${EINVAL}"; }

case "${CHUNK}" in
    ''|*[!0-9]*) usage >&2; exit "${EINVAL}" ;;
esac
# 下限拦截失控块数：1 MiB 的块对 3.4 GiB 就是 3400 个 layer，清单与推送
# 都会退化成不可维护的形态，这显然是调用方写错了参数。
if [ "${CHUNK}" -lt "${MIN_CHUNK}" ]; then
    note "--chunk-size ${CHUNK} 小于下限 ${MIN_CHUNK}"
    usage >&2
    exit "${EINVAL}"
fi

# 未指定 tag 时不是错误：image-build 的日常手动轮次只做组装与验收，不对外发布。
# 由调用方决定是否带 tag，门禁不替它猜。
if [ -z "${TAG}" ]; then
    note "未指定 --tag，跳过 OCI 发布"
    exit 0
fi

need_cmd oras python3 sha256sum stat numfmt split mktemp

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
count=$(( (size + CHUNK - 1) / CHUNK ))
say "ISO ${name}: sha256 ${sum}, $(numfmt --to=iec --suffix=B "${size}"), ${count} 块 x $(numfmt --to=iec --suffix=B "${CHUNK}")"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/airy-iso.XXXXXXXX")" ||
    die "无法创建分块暂存目录"
trap cleanup EXIT

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
publish=1

if fetch_remote; then
    have_layers="$(remote_parts | awk 'END {print NR}')"
    if [ "${have_layers}" -gt 0 ] && [ "${FORCE}" -eq 0 ]; then
        if [ "$(remote_field sha256)" = "${sum}" ] &&
            [ "$(remote_field chunk-size)" = "${CHUNK}" ] &&
            [ "$(remote_field chunks)" = "${count}" ]; then
            say "${target} 已发布同一份切分，跳过上传"
            publish=0
        else
            die "${target} 已存在但切分不同（sha256=$(remote_field sha256) chunks=$(remote_field chunks)）；确认要替换才加 --force"
        fi
    fi
fi

if [ "${publish}" -eq 1 ]; then
    group "切分 ${name} 为 ${count} 块"
    # split 的 -d -a 4 给出 part0000..partNNNN，位数固定，词序即块序。
    split -b "${CHUNK}" -d -a 4 "${iso}" "${WORK}/${name}.part" ||
        die "分块失败: ${iso}"
    parts_from_files
    endgroup

    push_args=()
    while IFS= read -r part_name; do
        push_args+=("${WORK}/${part_name}:${LAYER_TYPE}")
    done < <(cut -d' ' -f4 "${WORK}/parts.txt")
    parts_ann="$(awk '{printf "%s%s", sep, $2; sep = ","}' "${WORK}/parts.txt")"

    group "oras push ${target}"
    oras push --artifact-type "${ARTIFACT_TYPE}" \
        --annotation "${ANN}.name=${name}" \
        --annotation "${ANN}.sha256=${sum}" \
        --annotation "${ANN}.size=${size}" \
        --annotation "${ANN}.chunk-size=${CHUNK}" \
        --annotation "${ANN}.chunks=${count}" \
        --annotation "${ANN}.parts=${parts_ann}" \
        "${target}" "${push_args[@]}" || die "oras push 失败: ${target}"
    endgroup

    fetch_remote || die "推送后无法读取远端清单: ${target}"
    verify_remote
    say "远端校验通过: ${count} 块逐块 digest 一致"
else
    parts_from_remote
    [ -s "${WORK}/parts.txt" ] || die "远端清单缺少 layer: ${target}"
fi

ref_file="$(dirname "${iso}")/iso-oci.txt"
{
    printf 'ref=%s\n' "${REF}"
    printf 'tag=%s\n' "${TAG}"
    printf 'name=%s\n' "${name}"
    printf 'sha256=%s\n' "${sum}"
    printf 'size=%s\n' "${size}"
    printf 'chunk_size=%s\n' "${CHUNK}"
    printf 'chunks=%s\n' "${count}"
    while read -r idx pd ps pn; do
        printf 'part=%s sha256=%s size=%s name=%s\n' "${idx}" "${pd}" "${ps}" "${pn}"
    done <"${WORK}/parts.txt"
    printf 'pull=oras pull %s -o .\n' "${target}"
    printf 'join=cat %s.part* > %s\n' "${name}" "${name}"
} >"${ref_file}"

say "ISO 已发布: ${target}"
say "拉取: oras pull ${target} -o . 然后 cat ${name}.part* > ${name}"
say "引用记录: ${ref_file}"
