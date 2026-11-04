#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_image_verify.sh — ci/gates/image-verify.sh 的分支回归
#
# 这条门禁判的是「ISO 载荷里的内核包与发行 .sha256sum 是同一份」。0.0.4
# 组装期出过一次：派发时 release_tag=v0.0.3、树内已是 0.0.4，载荷是就地
# 编出的 0.0.4 内核，摘要文件却是 0.0.3 的；门禁判红了，但消息只说「与
# 发行校验和不一致」，现场照着它去查内核构建的随机性，而真正的原因是版本
# 错配。本回归锁住消息里的发行版本，并覆盖清单断言、来源指纹、数量下限与
# 参数校验，保证这条判据不会退回成一句无从下手的「不一致」。
#
# 用法: bash ci/tests/test_image_verify.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/image-verify.sh"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${ROOT}/ci/gates/image-verify.sh" "${GATE}"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 门禁在 CI 里把输出写进 $GITHUB_OUTPUT/$GITHUB_ENV；回归断言的是打印形态，
# 继承这些变量会让输出静默改道，本地全过、CI 全红。
run()
{
    env -u GITHUB_OUTPUT -u GITHUB_ENV -u GITHUB_ACTIONS \
        bash "${GATE}" "$@" 2>&1
}

# mkfy OUT SUMVER BID —— 造一份最小自洽产物：ISO + .sha256 + 两份清单 +
# 空载荷与空发行摘要。载荷条目由 rpm 逐条追加，方便各用例只改一处。
mkfy()
{
    local out="$1" sumver="$2" bid="$3" iso sum

    iso="${out}/desktop/iso/airymaxos-0.0.4-x86_64.iso"
    mkdir -p "${out}/desktop/iso" "${out}/rpms" \
        "${out}/desktop/payload/airymax" "${out}/rel"
    printf 'payload\n' >"${iso}"
    sum="$(sha256sum "${iso}" | cut -d' ' -f1)"
    printf '%s  %s\n' "${sum}" "${iso##*/}" >"${iso}.sha256"
    {
        printf 'stage rpm=pass\nstage iso=pass\n'
        printf 'stage kernel=reuse\nresult=pass\n'
        printf 'build_id=%s\n' "${bid}"
        printf 'iso %s %s\n' "${sum}" "${iso##*/}"
    } >"${out}/image-ci-manifest.txt"
    printf 'build_id=%s\n' "${bid}" >"${out}/rpms/build-manifest.txt"
    : >"${out}/rel/agent-linux-${sumver}.sha256sum"
}

# rpm OUT SUMVER NAME —— 追加一个载荷内核包；SUMVER 非空时同时写进该版
# 发行摘要，为空则只在载荷里（模拟「载荷有、发行没有」）。
rpm()
{
    local out="$1" sumver="$2" name="$3" sum

    sum="$(printf '%s' "${name}" | sha256sum | cut -d' ' -f1)"
    printf 'rpm %s %s\n' "${sum}" "${name}" \
        >>"${out}/desktop/payload/airymax/payload-manifest.txt"
    [ -z "${sumver}" ] ||
        printf '%s  %s\n' "${sum}" "${name}" \
        >>"${out}/rel/agent-linux-${sumver}.sha256sum"
}

# expect NAME WANT_RC TEXT [ARG...] —— 跑一次门禁，比对退出码与输出片段。
expect()
{
    local name="$1" want_rc="$2" want_text="$3" out rc
    shift 3
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" != "${want_rc}" ]; then
        bad "${name}（rc=${rc}，期望 ${want_rc}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${want_text}" ] && ! printf '%s' "${out}" | grep -qF -- "${want_text}"; then
        bad "${name}（输出缺「${want_text}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

NEVRA=(airymaxos-kernel-0.0.4-4.x86_64.rpm airymaxos-kernel-devel-0.0.4-4.x86_64.rpm
       airymaxos-kernel-modules-0.0.4-4.x86_64.rpm airymaxos-kernel-headers-0.0.4-4.x86_64.rpm)

# 1. 三包同源：判据成立时必须放行，否则门禁会把正确的组装判红。
o="${tmp}/same"
mkfy "${o}" 0.0.4 "r1-abc"
for k in "${NEVRA[@]:0:3}"; do rpm "${o}" 0.0.4 "${k}"; done
expect "三包与发行同源" 0 "一致: 3 个（v0.0.4）" \
    --out "${o}" --release-rpms "${o}/rel"

# 2. 版本错配：载荷是 0.0.4 内核，摘要却是 0.0.3 的（0.0.4 组装期的真实
#    形态）。消息必须点出对照的发行版本，否则无从判断是错配还是哈希分叉。
o="${tmp}/mismatch"
mkfy "${o}" 0.0.3 "r1-abc"
for k in "${NEVRA[@]:0:3}"; do rpm "${o}" "" "${k}"; done
expect "派发 tag 与树内版本错配" 1 "不在发行摘要 v0.0.3 中" \
    --out "${o}" --release-rpms "${o}/rel"

# 3. 同版本但哈希分叉：发行摘要版本对得上，包不匹配。消息里应出现同一版号。
o="${tmp}/fork"
mkfy "${o}" 0.0.4 "r1-abc"
for k in "${NEVRA[@]:0:3}"; do rpm "${o}" "" "${k}"; done
expect "同版本哈希分叉" 1 "不在发行摘要 v0.0.4 中" \
    --out "${o}" --release-rpms "${o}/rel"

# 4. 数量下限：两包同源也不够——少于 3 个说明载荷内核不是整份发行内核。
o="${tmp}/few"
mkfy "${o}" 0.0.4 "r1-abc"
for k in "${NEVRA[@]:0:2}"; do rpm "${o}" 0.0.4 "${k}"; done
expect "载荷内核包不足 3 个" 1 "少于 3" \
    --out "${o}" --release-rpms "${o}/rel"

# 5. 来源指纹：镜像清单与 RPM 清单同值才归因得起来。
o="${tmp}/bid"
mkfy "${o}" 0.0.4 "r1-abc"
for k in "${NEVRA[@]:0:3}"; do rpm "${o}" 0.0.4 "${k}"; done
printf 'build_id=r1-other\n' >"${o}/rpms/build-manifest.txt"
expect "来源指纹不一致" 1 "来源指纹不一致" \
    --out "${o}" --release-rpms "${o}/rel"

# 6. 复用发行包就必须报 reuse：就地构建的清单混进来会让「ISO 内核来自
#    发行」这条前提悄悄不成立。
o="${tmp}/stage"
mkfy "${o}" 0.0.4 "r1-abc"
expect "缺少 --release-rpms 时期望内核就地构建" 1 "stage kernel=pass" --out "${o}"

# 7. ISO 摘要与磁盘不符：清单记录的不是这份介质。
o="${tmp}/iso"
mkfy "${o}" 0.0.4 "r1-abc"
printf 'tampered\n' >"${o}/desktop/iso/airymaxos-0.0.4-x86_64.iso"
expect "ISO 摘要不一致" 1 "ISO 与清单摘要不一致" \
    --out "${o}" --expect-kernel reuse

# 8. 参数校验：缺 --out 与非法 --expect-kernel 都退 5，未知参数退 1。
expect "缺 --out" 5 "用法"
expect "非法 --expect-kernel" 5 "只接受 pass|reuse" --out "${tmp}" --expect-kernel maybe
expect "未知参数" 1 "未知参数" --out "${tmp}" --bogus

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
