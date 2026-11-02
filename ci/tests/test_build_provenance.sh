#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_build_provenance.sh — ci/gates/build-provenance.sh 的分支回归
#
# 真实树常年为绿，判定逻辑必须用负例驱动：逐条删掉七处接线中的一处，门禁
# 都必须变红。正例只保证没有误判，负例才保证没有漏判。断言只看退出码与
# 稳定判据名，不看措辞。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh build-provenance 负责那一条。
#
# 用法: bash ci/tests/test_build_provenance.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/release" \
    "${FX}/distro/scripts/lib" "${FX}/distro/specs" "${FX}/distro/ci" \
    "${FX}/.github/workflows"
cp "${ROOT}/ci/gates/build-provenance.sh" "${FX}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${FX}/ci/gates/build-provenance.sh" "$@" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

mkfix()
{
    cat >"${FX}/distro/release/os-release" <<'EOF'
NAME="OpenAirymax"
VERSION_ID="0.0.4"
BUILD_ID=""
EOF
    cat >"${FX}/distro/scripts/lib/buildid.sh" <<'EOF'
airy_bid_ok()
{
    :
}

airy_build_id()
{
    case "$1" in
    ''|*[!A-Za-z0-9._+-]*) return 1 ;;
    esac
}
EOF
    cat >"${FX}/distro/scripts/build-rpms.sh" <<'EOF'
. "${DISTRO_ROOT}/scripts/lib/buildid.sh"
BUILD_ID="$(airy_build_id "$DISTRO_ROOT" "${SPECS[@]}")"
DEFS+=("airy_build_id ${BUILD_ID}")
printf 'build_id=%s\n' "$BUILD_ID"
set_build_id
EOF
    cat >"${FX}/distro/specs/airymaxos-release.spec" <<'EOF'
%{!?airy_build_id:%global airy_build_id unset}
sed -i 's|^BUILD_ID=.*$|BUILD_ID="%{airy_build_id}"|' os-release
printf 'BUILD_ID=%s\n' '%{airy_build_id}' > %{_sysconfdir}/airymaxos-build
test '%{airy_build_id}' != unset
EOF
    cat >"${FX}/distro/ci/run-image-ci.sh" <<'EOF'
bid_get()
{
    printf 'build_id=%s\n' "${bid:-unset}"
}
EOF
    cat >"${FX}/ci/gates/image-verify.sh" <<'EOF'
die "镜像清单缺少构建来源指纹（build_id）"
die "来源指纹不一致: 镜像 ${bid} RPM ${built:-空}"
EOF
    cat >"${FX}/ci/gates/assemble.sh" <<'EOF'
for v in AIRYMAX_BUILD_ID AIRYMAX_RUN_ID AIRYMAX_COMMIT; do
    [ -n "${!v:-}" ] && envs+=(-e "${v}=${!v}")
done
EOF
    cat >"${FX}/.github/workflows/image-build.yml" <<'EOF'
        env:
          AIRYMAX_RUN_ID: ${{ github.run_id }}
          AIRYMAX_COMMIT: ${{ github.sha }}
EOF
    cat >"${FX}/distro/ci/run-smoke.sh" <<'EOF'
t18_build_id() {
    :
}

printf '1..18\n'
probe 18 "T18 build provenance" t18_build_id
EOF
}

# drop FILE PATTERN —— 删掉承载某处接线的那一行，模拟「顺手简化」。
drop()
{
    sed -i "\|$2|d" "$1"
}

mkfix

out="$(run --source "${FX}")"
check "接线完整判定通过" 0 "$?" "${out}" "构建来源接线通过"

out="$(run)"
check "无参数退 5" 5 "$?" "${out}" "--source 必填"

out="$(run --source "${tmp}/nope")"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

mkfix
drop "${FX}/distro/release/os-release" '^BUILD_ID='
out="$(run --source "${FX}")"
check "模板留空被删判拒绝" 1 "$?" "${out}" "BUILD_ID 模板应为空"

mkfix
printf 'BUILD_ID="src-abc"\n' >>"${FX}/distro/release/os-release"
out="$(run --source "${FX}")"
check "模板写死取值判拒绝" 1 "$?" "${out}" "应恰出现一次"

mkfix
drop "${FX}/distro/scripts/lib/buildid.sh" '^airy_build_id()'
out="$(run --source "${FX}")"
check "驱动缺 airy_build_id 判拒绝" 1 "$?" "${out}" "缺少 airy_build_id"

mkfix
rm -f "${FX}/distro/scripts/lib/buildid.sh"
out="$(run --source "${FX}")"
check "驱动文件缺失判拒绝" 1 "$?" "${out}" "缺文件"

mkfix
drop "${FX}/distro/scripts/build-rpms.sh" 'DEFS+=("airy_build_id'
out="$(run --source "${FX}")"
check "驱动未注入宏判拒绝" 1 "$?" "${out}" "未注入宏"

mkfix
drop "${FX}/distro/scripts/build-rpms.sh" '^set_build_id$'
out="$(run --source "${FX}")"
check "驱动未在装配前解析判拒绝" 1 "$?" "${out}" "未在装配前"

mkfix
drop "${FX}/distro/specs/airymaxos-release.spec" "test '%{airy_build_id}' != unset"
out="$(run --source "${FX}")"
check "配方未断言宏注入判拒绝" 1 "$?" "${out}" "未断言宏注入"

mkfix
drop "${FX}/distro/ci/run-image-ci.sh" '^bid_get()'
out="$(run --source "${FX}")"
check "清单未搬运指纹判拒绝" 1 "$?" "${out}" "缺少 bid_get"

mkfix
drop "${FX}/ci/gates/image-verify.sh" '缺少构建来源指纹'
out="$(run --source "${FX}")"
check "产物门禁未拒绝空指纹判拒绝" 1 "$?" "${out}" "未拒绝空指纹"

mkfix
drop "${FX}/ci/gates/assemble.sh" 'for v in AIRYMAX_BUILD_ID'
out="$(run --source "${FX}")"
check "容器未转发指纹判拒绝" 1 "$?" "${out}" "未转发指纹变量"

mkfix
drop "${FX}/.github/workflows/image-build.yml" 'AIRYMAX_RUN_ID:'
out="$(run --source "${FX}")"
check "流水线未传 run id 判拒绝" 1 "$?" "${out}" "未传 run id"

mkfix
drop "${FX}/distro/ci/run-smoke.sh" 'probe 18'
out="$(run --source "${FX}")"
check "探针未登记判拒绝" 1 "$?" "${out}" "T18 未登记"

mkfix
drop "${FX}/distro/ci/run-smoke.sh" "1..18"
out="$(run --source "${FX}")"
check "计划数未更新判拒绝" 1 "$?" "${out}" "计划数未更新"

if [ "${fails}" -ne 0 ]; then
    printf 'test_build_provenance: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_build_provenance: 全部通过\n'
