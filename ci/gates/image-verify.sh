#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/image-verify.sh — 核验组装产物清单自洽性
#
# 用法:
#   ci/gates/image-verify.sh --out DIR [--expect-kernel pass|reuse]
#                            [--release-rpms DIR] [--iso-rel SUB] [--payload-rel SUB]
#
# 校验链（任一步不符即失败）:
#   1. image-ci-manifest.txt 存在且 stage rpm/iso 为 pass、result=pass；
#   2. stage kernel 等于期望值（就地构建 pass / 复用发行包 reuse）；
#   3. 清单记录的 ISO 摘要与磁盘文件一致，且同名 .sha256 存在；
#   4. 给出 --release-rpms 时，载荷里每个内核包的摘要都必须命中发行
#      .sha256sum，且至少 3 个——这是「ISO 与 Release 是同一份内核」的
#      唯一判据。载荷再编一次内核就会同名包哈希分叉，此处必红。
#
# 之所以独立成 gate：这些断言此前内联在 image-ci.yml 里，只能靠两小时的
# 云端 job 验证。抽成脚本后可在任何一台留有产物的机器上重放（本仓库首次
# 用它是在 B50 现场机对 0.0.3 的 ISO 复算）。
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

OUT=""
EXPECT_KERNEL=""
REL_RPMS="${AIRYMAX_REL_RPMS:-}"
ISO_REL="desktop/iso"
PAYLOAD_REL="desktop/payload/airymax"

usage()
{
    cat <<'EOF'
用法: ci/gates/image-verify.sh --out DIR [--expect-kernel pass|reuse]
                               [--release-rpms DIR]
                               [--iso-rel SUB] [--payload-rel SUB]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)          OUT="${2:-}"; shift 2 ;;
        --expect-kernel) EXPECT_KERNEL="${2:-}"; shift 2 ;;
        --release-rpms) REL_RPMS="${2:-}"; shift 2 ;;
        --iso-rel)      ISO_REL="${2:-}"; shift 2 ;;
        --payload-rel)  PAYLOAD_REL="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${OUT}" ] || { usage >&2; exit "${EINVAL}"; }

# 缺省期望由来源决定：复用了发行内核包就是 reuse，就地构建才是 pass。
# 让调用方手填这个值，等于给它一次写错的机会。
if [ -z "${EXPECT_KERNEL}" ]; then
    if [ -n "${REL_RPMS}" ]; then EXPECT_KERNEL=reuse; else EXPECT_KERNEL=pass; fi
fi
case "${EXPECT_KERNEL}" in pass|reuse) ;; *)
    err "--expect-kernel 只接受 pass|reuse"; exit "${EINVAL}" ;; esac

man="${OUT}/image-ci-manifest.txt"
[ -f "${man}" ] || die "缺少镜像清单: ${man}"
say "清单 ${man}"
cat "${man}"

expect_line()
{
    grep -qx "$1" "${man}" || die "清单断言失败: 期望 '$1'"
}

expect_line "stage rpm=pass"
expect_line "stage iso=pass"
expect_line "stage kernel=${EXPECT_KERNEL}"
expect_line "result=pass"

line="$(grep -m1 '^iso ' "${man}" || true)"
[ -n "${line}" ] || die "清单缺少 ISO 记录"
# shellcheck disable=SC2086
read -r _ sum name <<<"${line}"
iso="${OUT}/${ISO_REL}/${name}"
[ -f "${iso}" ] || die "ISO 不存在: ${iso}"

got="$(sha256sum "${iso}" | cut -d' ' -f1)"
[ "${got}" = "${sum}" ] || die "ISO 与清单摘要不一致: 期望 ${sum} 实得 ${got}"
[ -f "${iso}.sha256" ] || die "缺少 ISO 校验和文件: ${iso}.sha256"
say "ISO 摘要一致: ${got} ${name}"

if [ -n "${REL_RPMS}" ]; then
    payload="${OUT}/${PAYLOAD_REL}/payload-manifest.txt"
    [ -f "${payload}" ] || die "缺少载荷清单: ${payload}"
    shopt -s nullglob
    sums=("${REL_RPMS}"/agent-linux-*.sha256sum)
    shopt -u nullglob
    [ "${#sums[@]}" -gt 0 ] || die "发行目录没有 agent-linux-*.sha256sum"

    n=0
    while read -r kind psum pname; do
        [ "${kind}" = rpm ] || continue
        case "${pname}" in airymaxos-kernel*) ;; *) continue ;; esac
        hit=0
        for s in "${sums[@]}"; do
            if grep -qxF "${psum}  ${pname}" "${s}"; then
                hit=1
                break
            fi
        done
        [ "${hit}" -eq 1 ] || die "${pname} 与发行校验和不一致"
        n=$(( n + 1 ))
    done <"${payload}"

    [ "${n}" -ge 3 ] || die "载荷内核包仅 ${n} 个，少于 3"
    say "载荷内核包与发行校验和一致: ${n} 个"
fi

say "组装产物校验通过"
