#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_pool_seed.sh — ci/gates/pool-seed.sh 的分支回归
#
# 本机与 CI 都拿不到 oras，也不能指望 ghcr.io 在测试时可达，因此用一个
# 只认 pull/login 的假 oras 驱动四条分支：池非空跳过、制品尚未发布回落、
# 摘要一致注入、摘要不符拒绝注入。非空判据与 ci/gates/pool-state.sh 一致
# （任意 *.rpm），测试因此直接落一个空文件当 RPM。
#
# 用法: bash ci/tests/test_pool_seed.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/pool-seed.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

mkdir -p "${tmp}/bin"
cat >"${tmp}/bin/oras" <<'EOF'
#!/bin/sh
cmd="$1"
shift
case "$cmd" in
login)
    cat >/dev/null
    exit 0
    ;;
pull)
    out=""
    while [ $# -gt 0 ]; do
        case "$1" in
        -o) shift; out=$1 ;;
        esac
        shift
    done
    if [ ! -d "${FAKE_ART:-}" ]; then
        echo "Error: ${FAKE_ERR:-manifest unknown}" >&2
        exit 1
    fi
    mkdir -p "$out"
    cp -a "${FAKE_ART}/." "$out"/
    ;;
esac
exit 0
EOF
chmod 755 "${tmp}/bin/oras"

seed()
{
    PATH="${tmp}/bin:${PATH}" bash "${GATE}" --dir "$1" 2>&1
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

# 完整性判据是完成标记而不是「目录里有没有 RPM」：中止的轮次会把半份池
# 存进缓存，下一轮 restore 命中的正是那份半份池，以非空判完整就会跳过
# 播种（run 41075519752）。因此这两种形态必须分别断言。
mkdir -p "${tmp}/marked"
: >"${tmp}/marked/fake-1.0-1.noarch.rpm"
: >"${tmp}/marked/.seed-complete"
out="$(seed "${tmp}/marked")"
check "完成标记存在时跳过播种" 0 "$?" "${out}" "池已完整播种"

mkdir -p "${tmp}/half"
: >"${tmp}/half/fake-1.0-1.noarch.rpm"
out="$(FAKE_ART="" seed "${tmp}/half")"
check "缺完成标记按清单补差" 0 "$?" "${out}" "缺完成标记"

mkdir -p "${tmp}/empty"
out="$(FAKE_ART="" seed "${tmp}/empty")"
check "制品未发布回落在线求解" 0 "$?" "${out}" "尚未发布"

mkdir -p "${tmp}/pkg"
echo "fake rpm" >"${tmp}/pkg/fake-1.0-1.noarch.rpm"
mkdir -p "${tmp}/art"
tar -C "${tmp}/pkg" -cf "${tmp}/art/pool-24.03.tar" .
( cd "${tmp}/pkg" && sha256sum ./*.rpm ) >"${tmp}/art/pool-manifest.txt"
mkdir -p "${tmp}/target"
out="$(FAKE_ART="${tmp}/art" seed "${tmp}/target")"
check "摘要一致注入" 0 "$?" "${out}" "sha256 全部一致"
[ -f "${tmp}/target/fake-1.0-1.noarch.rpm" ] &&
    ok "注入后池内有 RPM" || bad "注入后池内没有 RPM"

sed 's/^[0-9a-f]\{64\}/0000000000000000000000000000000000000000000000000000000000000000/' \
    "${tmp}/art/pool-manifest.txt" >"${tmp}/art/bad-manifest.txt"
cp "${tmp}/art/bad-manifest.txt" "${tmp}/art/pool-manifest.txt"
mkdir -p "${tmp}/reject"
out="$(FAKE_ART="${tmp}/art" seed "${tmp}/reject")"
check "摘要不符拒绝注入" 1 "$?" "${out}" "sha256 校验失败"
[ -z "$(find "${tmp}/reject" -name '*.rpm' | head -n 1)" ] &&
    ok "拒绝注入后池为空" || bad "摘要不符仍有 RPM 进池"

exit "${fails}"
