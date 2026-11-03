#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_payload_publish.sh — ci/gates/payload-publish.sh 的分支回归
#
# registry 在测试时不可达，oras 也不保证安装，因此用一个把制品落在本地目录
# 的假 oras 驱动各条分支：审计失败拒推、幂等跳过、回读通过、回读摘要不符
# 后删除 tag、制品未发布回落、逐包不符只丢该包。它同时守住「同一批文件两次
# 打包得到同一摘要」——tag 的内容寻址语义全系于这一点。
#
# 用法: bash ci/tests/test_payload_publish.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/payload-publish.sh"
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

mkdir -p "${tmp}/store"
# shellcheck source=lib/fake_oras.sh
. "${ROOT}/ci/tests/lib/fake_oras.sh"
fake_oras_install "${tmp}/bin"

run()
{
    # 假 oras 的存储与污染开关按需透传：前缀赋值只作用于函数，未必进得到
    # 子进程，显式导出才不必依赖各分支的上文。
    FAKE_STORE="${FAKE_STORE:-${tmp}/store}" FAKE_CORRUPT="${FAKE_CORRUPT:-}" \
        PATH="${tmp}/bin:${PATH}" bash "${GATE}" "$@" 2>&1
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

mkdir -p "${tmp}/pkg"
printf 'a' >"${tmp}/pkg/a.rpm"
printf 'bb' >"${tmp}/pkg/b.rpm"
sum_a="$(sha256sum "${tmp}/pkg/a.rpm" | cut -d' ' -f1)"
sum_b="$(sha256sum "${tmp}/pkg/b.rpm" | cut -d' ' -f1)"
mkman()
{
    {
        printf '# AirymaxOS payload manifest\n'
        printf 'arch=%s\n' "${2:-x86_64}"
        printf 'count=%s\n' "${1:-2}"
        printf 'comps 0000000000000000000000000000000000000000000000000000000000000000 comps/airymaxos-comps.xml\n'
        printf 'rpm %s a.rpm\n' "${sum_a}"
        printf 'rpm %s b.rpm\n' "${sum_b}"
    } >"${tmp}/man.txt"
}
mkman 2

out="$(run push --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" \
    --tag build-0.0.4-abc --ref "ghcr.io/x/y")"
check "审计通过并回读" 0 "$?" "${out}" "回读校验通过"

out="$(run push --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" \
    --tag build-0.0.4-abc --ref "ghcr.io/x/y")"
check "同 tag 幂等跳过" 0 "$?" "${out}" "跳过推送"

out="$(run push --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" \
    --tag build-0.0.4-def --ref "ghcr.io/x/y")"
layer1="$(printf '%s' "${out}" | sed -n 's/^payload-publish: 载荷层 sha256: //p')"
out="$(run push --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" \
    --tag build-0.0.4-ghi --ref "ghcr.io/x/y")"
layer2="$(printf '%s' "${out}" | sed -n 's/^payload-publish: 载荷层 sha256: //p')"
if [ -n "${layer1}" ] && [ "${layer1}" = "${layer2}" ]; then
    ok "同批文件两次打包摘要一致"
else
    bad "打包不可复现（${layer1:-空} vs ${layer2:-空}）"
fi

mkman 3
out="$(run push --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" \
    --tag build-0.0.4-bad --ref "ghcr.io/x/y")"
check "count 不符拒绝推送" 1 "$?" "${out}" "计数不符"
[ ! -d "${tmp}/store/ghcr.io_x_y_build-0.0.4-bad" ] &&
    ok "拒推后未留下 tag" || bad "拒推后 tag 仍然存在"

mkman 2
printf 'zz' >"${tmp}/pkg/b.rpm"
out="$(run push --dir "${tmp}/pkg" --manifest "${tmp}/man.txt" \
    --tag build-0.0.4-xyz --ref "ghcr.io/x/y")"
check "逐包摘要不符拒绝推送" 1 "$?" "${out}" "sha256 不符"
printf 'bb' >"${tmp}/pkg/b.rpm"

out="$(FAKE_CORRUPT=1 run push --dir "${tmp}/pkg" \
    --manifest "${tmp}/man.txt" --tag build-0.0.4-corrupt \
    --ref "ghcr.io/x/y")"
check "回读不符删除 tag 并失败" 1 "$?" "${out}" "不可信"
[ ! -d "${tmp}/store/ghcr.io_x_y_build-0.0.4-corrupt" ] &&
    ok "回读不符后 tag 已删除" || bad "回读不符后 tag 仍在"

out="$(run pull --dir "${tmp}/in" --tag seed-x86_64-nope --ref "ghcr.io/x/y")"
check "制品未发布回落上游" 0 "$?" "${out}" "回落上游求解"

mkdir -p "${tmp}/in2"
out="$(run pull --dir "${tmp}/in2" --tag build-0.0.4-abc --ref "ghcr.io/x/y")"
check "命中回取" 0 "$?" "${out}" "回取 2/2"
[ -f "${tmp}/in2/a.rpm" ] && [ -f "${tmp}/in2/.payload-source" ] &&
    ok "回取后池内有 RPM 与来源标记" || bad "回取后缺 RPM 或来源标记"

mkdir -p "${tmp}/store2"
mv "${tmp}/store/ghcr.io_x_y_build-0.0.4-abc" "${tmp}/store2/"
sed "s/^rpm ${sum_b} b\.rpm\$/rpm 0000000000000000000000000000000000000000000000000000000000000000 b.rpm/" \
    "${tmp}/store2/ghcr.io_x_y_build-0.0.4-abc/payload-manifest.txt" \
    >"${tmp}/store2/ghcr.io_x_y_build-0.0.4-abc/man.tmp"
mv "${tmp}/store2/ghcr.io_x_y_build-0.0.4-abc/man.tmp" \
    "${tmp}/store2/ghcr.io_x_y_build-0.0.4-abc/payload-manifest.txt"
out="$(FAKE_STORE="${tmp}/store2" run pull --dir "${tmp}/in3" \
    --tag build-0.0.4-abc --ref "ghcr.io/x/y")"
check "单包不符只丢弃该包" 0 "$?" "${out}" "丢弃 b.rpm"
[ -f "${tmp}/in3/a.rpm" ] && [ ! -f "${tmp}/in3/b.rpm" ] &&
    ok "不符的包未进池" || bad "不符的包进了池"

exit "${fails}"
