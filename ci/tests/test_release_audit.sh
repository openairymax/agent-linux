#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_audit.sh — 资产集合两道门禁的分支回归
#
# release-audit.sh（写入后对账）与 release-prune.sh（写入前收回）共用
# ci/lib/release-assets.sh 的期望集，因此用同一份清单夹具同时钉住两侧：
# 期望集只算一次，两侧的差集语义必须互补——对账报缺件，剪枝报多余项。
#
# 对账的判据是双向的（缺失与多余都失败），而两种失败在页面上都表现
# 为「发布成功」——只有断言能区分。curl 用桩顶替：桩从文件取响应体与
# HTTP 码，门禁因此可以在不联网的情况下走完 200 / 404 / 缺件 / 多件
# 四条路径。
#
# 用法: bash ci/tests/test_release_audit.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/out" "${tmp}/stub"
cp "${ROOT}/ci/gates/release-audit.sh" "${FX}/ci/gates/release-audit.sh"
cp "${ROOT}/ci/gates/release-prune.sh" "${FX}/ci/gates/release-prune.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/release-assets.sh" "${FX}/ci/lib/release-assets.sh"

cat >"${tmp}/stub/curl" <<'EOF'
#!/usr/bin/env bash
out=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        *)  shift ;;
    esac
done
[ -n "${out}" ] && [ "${out}" != "/dev/null" ] && cp "${STUB_BODY}" "${out}"
printf '%s' "${STUB_CODE}"
EOF
chmod +x "${tmp}/stub/curl"

export STUB_BODY="${tmp}/body" STUB_CODE=200
export PATH="${tmp}/stub:${PATH}"
: >"${STUB_BODY}"

SUMS="agent-linux-0.0.4.sha256sum"
NOTES="agent-linux-0.0.4-release-notes.md"
{
    printf '%064d  %s\n' 0 a.rpm
    printf '%064d  %s\n' 0 a.rpm.asc
    printf '%064d  %s\n' 0 b.rpm
    printf '%064d  %s\n' 0 b.rpm.asc
    printf '%064d  %s\n' 0 oci-image-v0.0.4.tar
} >"${FX}/${SUMS}"

# 期望集按清单原样取，并扣除只经 ghcr 交付的 OCI 镜像；`*.asc` 已是独立
# 条目，不能再补一次后缀。
WANT=("${SUMS}" "${SUMS}.asc" "${NOTES}" "${NOTES}.asc"
      a.rpm a.rpm.asc b.rpm b.rpm.asc)

assets()
{
    printf '{"assets":['
    local i=0 name
    for name in "$@"; do
        [ "${i}" -gt 0 ] && printf ','
        printf '{"id":%d,"name":"%s"}' "$((i + 1))" "${name}"
        i=$((i + 1))
    done
    printf ']}'
}

check()
{
    local name="$1" want_rc="$2" text="$3"
    shift 3
    local out rc
    out="$(TOKEN=x bash "${FX}/ci/gates/release-audit.sh" --tag v0.0.4 --repo o/r --dir "${FX}" "$@" 2>&1)"
    rc=$?
    if [ "${rc}" -ne "${want_rc}" ]; then
        printf 'FAIL %s: 退出码 %d，期望 %d\n%s\n' "${name}" "${rc}" "${want_rc}" "${out}" >&2
        fails=$((fails + 1))
        return
    fi
    if [ -n "${text}" ] && ! printf '%s' "${out}" | grep -Fq -- "${text}"; then
        printf 'FAIL %s: 输出缺少 %s\n%s\n' "${name}" "${text}" "${out}" >&2
        fails=$((fails + 1))
        return
    fi
    printf 'ok   %s\n' "${name}"
}

assets "${WANT[@]}" >"${STUB_BODY}"
check "页面与清单完全一致" 0 "一致"

assets "${SUMS}" "${SUMS}.asc" "${NOTES}" "${NOTES}.asc" \
       a.rpm a.rpm.asc b.rpm >"${STUB_BODY}"
check "缺失一项判失败" 1 "缺少 1 项"

assets "${WANT[@]}" c.rpm >"${STUB_BODY}"
check "多出一项判失败" 1 "多出 1 项"

# OCI 镜像包在清单里、但走 ghcr：出现在页面上本身就是发布口径错误。
assets "${WANT[@]}" oci-image-v0.0.4.tar >"${STUB_BODY}"
check "ghcr 载荷不得上页" 1 "oci-image-v0.0.4.tar"

STUB_CODE=404
check "Release 不存在退 3" 3 "不存在"

STUB_CODE=200
# 无令牌时匿名 GET 会先撞上 runner 共享出口 IP 的配额，403 会被读成「读不到
# 页面」而不是「缺件」。对账必须显式带令牌，缺令牌在本地就退 5。
out="$(env -u TOKEN -u GITHUB_TOKEN -u GH_TOKEN bash "${FX}/ci/gates/release-audit.sh" \
    --tag v0.0.4 --repo o/r --dir "${FX}" 2>&1)"
rc=$?
if [ "${rc}" -eq 5 ]; then
    printf 'ok   缺少令牌退 5\n'
else
    printf 'FAIL 缺少令牌: 退出码 %d\n%s\n' "${rc}" "${out}" >&2
    fails=$((fails + 1))
fi

out="$(bash "${FX}/ci/gates/release-audit.sh" --repo o/r --dir "${FX}" 2>&1)"
rc=$?
if [ "${rc}" -eq 5 ]; then
    printf 'ok   缺少 --tag 退 5\n'
else
    printf 'FAIL 缺少 --tag: 退出码 %d\n%s\n' "${rc}" "${out}" >&2
    fails=$((fails + 1))
fi

# 剪枝与对账互补：同一份期望集下，页面上的陈旧项只由剪枝处置。
assets "${WANT[@]}" c.rpm >"${STUB_BODY}"
out="$(bash "${FX}/ci/gates/release-prune.sh" --tag v0.0.4 --repo o/r --dir "${FX}" --dry-run 2>&1)"
rc=$?
if [ "${rc}" -eq 0 ] && printf '%s' "${out}" | grep -Fq -- "待删除 c.rpm"; then
    printf 'ok   剪枝演练只列出陈旧项\n'
else
    printf 'FAIL 剪枝演练: 退出码 %d\n%s\n' "${rc}" "${out}" >&2
    fails=$((fails + 1))
fi

{ printf '%064d  x.rpm\n' 0; } >"${FX}/agent-linux-0.0.5.sha256sum"
out="$(TOKEN=x bash "${FX}/ci/gates/release-audit.sh" --tag v0.0.4 --repo o/r --dir "${FX}" 2>&1)"
rc=$?
if [ "${rc}" -eq 5 ]; then
    printf 'ok   清单不唯一退 5\n'
else
    printf 'FAIL 清单不唯一: 退出码 %d\n%s\n' "${rc}" "${out}" >&2
    fails=$((fails + 1))
fi

if [ "${fails}" -eq 0 ]; then
    printf 'test_release_audit: 全部通过\n'
else
    printf 'test_release_audit: %d 项失败\n' "${fails}" >&2
fi
exit "$((fails > 0))"
