#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_rpms.sh — ci/gates/release-rpms.sh 的分支回归
#
# 本门禁决定 ISO 载荷里的内核包来自哪一次构建。它一旦取错，装配出来的介质
# 与发行校验和不同源，而失败要等到装机才暴露；取件口又依赖 gh 与远端制品，
# 本地无法直连。因此按协议造假 gh：把预置目录当成 release-artifacts-<ver>
# 制品或发布页资产复制出来，判定逻辑在没有网络、没有 token 的机器上逐条
# 打穿。
#
# 两条取件路径（--run-id 的制品 / 发布页资产）在磁盘上布局不同：制品保留
# dist/rpm/<arch>/ 层级，发布页是平铺的。摘要清单只有一份，校验与计数都
# 必须在清单所在位置成立，不能各自假定布局。
#
# 用法: bash ci/tests/test_release_rpms.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/release-rpms.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
FAKE="${tmp}/fake"
OUT="${tmp}/out"

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/bin"
cp "${GATE}" "${FX}/ci/gates/release-rpms.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

# expect NAME RC TEXT [ARG...] —— 跑一次门禁，比对退出码与输出片段。
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
    case "${out}" in
    *"${want_text}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${want_text}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

run()
{
    env -u GITHUB_ENV -u GITHUB_OUTPUT -u GITHUB_ACTIONS \
        PATH="${FX}/bin:${PATH}" FAKE="${FAKE}" \
        bash "${FX}/ci/gates/release-rpms.sh" "$@" 2>&1
}

# 假 gh：只实现本门禁用到的两条子命令，其余退 2，避免调用面扩大而测试仍绿。
#
# 未知开关一律按真实 CLI 的口径退 1，不静默跳过。这里曾用 `*) shift` 把不
# 认识的参数吞掉，于是门禁传了 gh run download 根本没有的 --clobber 仍全绿，
# 缺陷一路滑到装配 ISO 的流水线上才以「无制品」的假象爆出来。替身比被替身
# 宽松，测试就不再是测试，而是给错误发通行证。
cat >"${FX}/bin/gh" <<'EOF'
#!/usr/bin/env bash
flag_die()
{
    printf 'unknown flag: %s\n' "$1" >&2
    exit 1
}

case "${1:-} ${2:-}" in
"run download")
    shift 2
    id="${1:-}"
    shift
    name="" dir=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
        --name) name="${2:-}"; shift 2 ;;
        --dir)  dir="${2:-}"; shift 2 ;;
        --repo) shift 2 ;;
        *)      flag_die "$1" ;;
        esac
    done
    [ -d "${FAKE}/art/${id}/${name}" ] || exit 1
    mkdir -p "${dir}"
    cp -a "${FAKE}/art/${id}/${name}/." "${dir}/"
    ;;
"release download")
    shift 2
    # 首个位置参数是 TAG，真实 CLI 靠它定位发布页；替身按 FAKE/rel 取件，
    # 但必须把它吞掉，否则会被当成未知开关。
    case "${1:-}" in '' | --*) ;; *) shift ;; esac
    dir=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
        --dir)     dir="${2:-}"; shift 2 ;;
        --clobber) shift ;;
        --pattern) shift 2 ;;
        --repo)    shift 2 ;;
        *)         flag_die "$1" ;;
        esac
    done
    [ -d "${FAKE}/rel" ] || exit 1
    mkdir -p "${dir}"
    cp -a "${FAKE}/rel/." "${dir}/"
    ;;
*) exit 2 ;;
esac
EOF
chmod +x "${FX}/bin/gh"

# mkfix — 每例从空制品根与空输出目录开始。
mkfix()
{
    rm -rf "${FAKE}" "${OUT}"
    mkdir -p "${FAKE}" "${OUT}"
}

# rpm <路径> — 造一个内容唯一的内核包，摘要因此可区分。
rpm()
{
    mkdir -p "$(dirname "$1")"
    printf 'rpm:%s\n' "$1" >"$1"
}

# sums <清单> <目录> <文件名>... — 按 sha256sum 格式写清单（只写文件名）。
sums()
{
    local list="$1" dir="$2" f

    shift 2
    : >"${list}"
    for f in "$@"; do
        printf '%s  %s\n' \
            "$(sha256sum "${dir}/${f}" | cut -d' ' -f1)" "${f}" >>"${list}"
    done
}

A=x86_64
K1="airymaxos-kernel-0.0.4-3.${A}.rpm"
K2="airymaxos-kernel-devel-0.0.4-3.${A}.rpm"
AR="airymaxos-kernel-0.0.4-3.aarch64.rpm"
SUM="agent-linux-0.0.4.sha256sum"

# ---- 制品路径：dist/rpm/<arch>/ 层级保留，清单在制品根 ----
mkfix
D="${FAKE}/art/77/release-artifacts-0.0.4/dist/rpm/${A}"
rpm "${D}/${K1}"
rpm "${D}/${K2}"
rpm "${FAKE}/art/77/release-artifacts-0.0.4/dist/rpm/aarch64/${AR}"
sums "${FAKE}/art/77/release-artifacts-0.0.4/${SUM}" "${D}" "${K1}" "${K2}" \
    >/dev/null
sha256sum "${FAKE}/art/77/release-artifacts-0.0.4/dist/rpm/aarch64/${AR}" |
    sed "s#.*/##" >>"${FAKE}/art/77/release-artifacts-0.0.4/${SUM}"
expect "制品路径按清单定位到架构目录" 0 "发行内核包就绪: 2 个" \
    --tag v0.0.4 --repo o/r --dir "${OUT}" --run-id 77
# 出口指向下载落盘目录而不是制品源目录：下游在 GH 环境里读的就是这个路径。
expect "取件目录写进环境出口" 0 "export AIRYMAX_REL_RPMS=${OUT}/dist/rpm/${A}" \
    --tag v0.0.4 --repo o/r --dir "${OUT}" --run-id 77

mkfix
D="${FAKE}/art/78/release-artifacts-0.0.4/dist/rpm/aarch64"
A2=aarch64
rpm "${D}/${AR}"
rpm "${FAKE}/art/78/release-artifacts-0.0.4/dist/rpm/${A}/${K1}"
sums "${FAKE}/art/78/release-artifacts-0.0.4/${SUM}" "${D}" "${AR}" >/dev/null
sha256sum "${FAKE}/art/78/release-artifacts-0.0.4/dist/rpm/${A}/${K1}" |
    sed "s#.*/##" >>"${FAKE}/art/78/release-artifacts-0.0.4/${SUM}"
expect "换架构只取该架构的包" 0 "发行内核包就绪: 1 个" \
    --tag v0.0.4 --repo o/r --dir "${OUT}" --run-id 78 --arch "${A2}"

# ---- 发布页路径：平铺，清单与包同目录 ----
mkfix
D="${FAKE}/rel"
rpm "${D}/${K1}"
sums "${D}/${SUM}" "${D}" "${K1}" >/dev/null
expect "发布页平铺布局" 0 "发行内核包就绪: 1 个" --tag v0.0.4 --repo o/r \
    --dir "${OUT}"

# ---- 判据 ----
mkfix
D="${FAKE}/art/81/release-artifacts-0.0.4/dist/rpm/${A}"
rpm "${D}/${K1}"
sums "${FAKE}/art/81/release-artifacts-0.0.4/${SUM}" "${D}" "${K1}" >/dev/null
printf 'tampered\n' >>"${D}/${K1}"
expect "包被改写判摘要不符" 1 "摘要不符" --tag v0.0.4 --repo o/r \
    --dir "${OUT}" --run-id 81

mkfix
D="${FAKE}/art/82/release-artifacts-0.0.4/dist/rpm/${A}"
rpm "${D}/${K1}"
sums "${FAKE}/art/82/release-artifacts-0.0.4/${SUM}" "${D}" "${K1}" >/dev/null
rpm "${D}/airymaxos-kernel-0.0.4-9.${A}.rpm"
expect "多出的内核包判计数不符" 1 "校验和登记 1 个" --tag v0.0.4 --repo o/r \
    --dir "${OUT}" --run-id 82

mkfix
D="${FAKE}/art/83/release-artifacts-0.0.4/dist/rpm/${A}"
rpm "${D}/${K1}"
sums "${FAKE}/art/83/release-artifacts-0.0.4/${SUM}" "${D}" "${K1}" >/dev/null
expect "清单未登记该架构" 1 "未登记 aarch64 内核包" --tag v0.0.4 --repo o/r \
    --dir "${OUT}" --run-id 83 --arch aarch64

mkfix
D="${FAKE}/art/84/release-artifacts-0.0.4/dist/rpm/${A}"
rpm "${D}/${K1}"
expect "缺校验和清单" 1 "未提供" --tag v0.0.4 --repo o/r --dir "${OUT}" \
    --run-id 84

expect "run 无该制品" 1 "无 release-artifacts-0.0.4 制品" --tag v0.0.4 \
    --repo o/r --dir "${OUT}" --run-id 85

expect "run-id 非数字退 5" 5 "--run-id 必须是数字" --tag v0.0.4 --repo o/r \
    --dir "${OUT}" --run-id 8a

expect "缺 --dir 退 5" 5 "均为必填" --tag v0.0.4 --repo o/r

expect "架构名非法退 5" 5 "RPM 架构名" --tag v0.0.4 --repo o/r \
    --dir "${OUT}" --arch x86-64

expect "未知参数退 5" 5 "未知参数" --tag v0.0.4 --repo o/r --dir "${OUT}" \
    --bogus

if [ "${fails}" -ne 0 ]; then
    printf 'test_release_rpms: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_release_rpms: 全部通过\n'
