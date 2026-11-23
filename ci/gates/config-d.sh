#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/config-d.sh — A-UCS 统一配置守护的装载与契约门禁
#
# 用法:
#   ci/gates/config-d.sh
#
# 断言链（缺一不可）：
#   1. 出树构建 config_d，构建目录不得落在源码树内。
#   2. 出厂 /etc/agentrt/agentrt.conf 与内置默认值逐键一致：两份默认值
#      各说各话时，装机后的实际行为取决于读的是哪一份。
#   3. 出厂配置通过完整校验。
#   4. 拒绝路径的退出码可归因：版本闸门 101、schema 102、主配置缺失 105。
#   5. conf.d 只认 NN- 前缀并按文件名字典序叠加。
#   6. unit 契约：notify 就绪、--root 指向 /etc/agentrt，且无残留的旧
#      /etc/agentrt/daemons/ 引用。
#
# 退出码: 0 通过；1 判定失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

ETC="${ROOT}/services/daemons/config_d/etc"
UNIT="${ROOT}/system/units/agentrt-config.service"
BUILD="${AIRY_CFG_BUILD_ROOT:-${TMPDIR:-/tmp}/airy-cfg-verify}"

case "${BUILD}" in
    "${ROOT}"/*) die "构建目录不得位于源码树内: ${BUILD}" ;;
esac

need_cmd cmake
need_cmd diff

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

expect_rc()
{
    local desc="$1" want="$2" out rc
    shift 2
    out="$("$@" 2>&1)"
    rc=$?
    [ "${rc}" -eq "${want}" ] ||
        die "${desc}: 退出码 ${rc}，期望 ${want}（${out}）"
    say "${desc}: rc=${rc}"
}

# ─── 1. 出树构建 ─────────────────────────────────────────────────────
group "config_d: 出树构建"
if ! cmake -S "${ROOT}/services" -B "${BUILD}" \
        -DCMAKE_BUILD_TYPE=Release >/dev/null; then
    endgroup
    die "services 配置失败（构建目录 ${BUILD}）"
fi
cmake --build "${BUILD}" --target config_d --parallel 2>&1 | tail -n 30
rc=${PIPESTATUS[0]}
endgroup
[ "${rc}" -eq 0 ] || die "config_d 构建失败（构建目录 ${BUILD}）"

BIN="${BUILD}/daemons/config_d/config_d"
[ -x "${BIN}" ] || die "未生成 config_d: ${BIN}"

# ─── 2. 出厂配置与内置默认值一致 ─────────────────────────────────────
group "config_d: 出厂配置与内置默认值一致"
"${BIN}" --defaults > "${WORK}/defaults.txt" ||
    die "--defaults 失败"
"${BIN}" --dump --root "${ETC}" > "${WORK}/shipped.txt" ||
    die "出厂 agentrt.conf 装载失败"
diff -u "${WORK}/defaults.txt" "${WORK}/shipped.txt" >&2 ||
    die "出厂 agentrt.conf 与内置默认值漂移"
endgroup
say "出厂配置与内置默认值逐键一致"

# ─── 3. 出厂配置校验 ─────────────────────────────────────────────────
group "config_d: 出厂配置校验"
"${BIN}" --check --root "${ETC}" || die "出厂 agentrt.conf 未通过校验"
endgroup
say "出厂配置校验通过"

# ─── 4. 拒绝路径 ─────────────────────────────────────────────────────
group "config_d: 拒绝路径"
mkdir -p "${WORK}/ver" "${WORK}/schema" "${WORK}/missing"
cp "${ETC}/agentrt.conf" "${WORK}/ver/agentrt.conf"
sed -i 's/^version = 1$/version = 2/' "${WORK}/ver/agentrt.conf"
expect_rc "版本闸门" 101 "${BIN}" --check --root "${WORK}/ver"

cp "${ETC}/agentrt.conf" "${WORK}/schema/agentrt.conf"
printf 'no_such_key = 1\n' >> "${WORK}/schema/agentrt.conf"
expect_rc "未知键" 102 "${BIN}" --check --root "${WORK}/schema"

expect_rc "缺主配置" 105 "${BIN}" --check --root "${WORK}/missing"
endgroup

# ─── 5. conf.d 叠加顺序与命名 ────────────────────────────────────────
group "config_d: conf.d 叠加顺序"
cp -r "${ETC}/." "${WORK}/drop/"
mkdir -p "${WORK}/drop/conf.d"
printf '[log]\nring_capacity = 2048\n' > "${WORK}/drop/conf.d/10-a.conf"
printf '[log]\nring_capacity = 8192\n' > "${WORK}/drop/conf.d/20-b.conf"
printf '[log]\nring_capacity = 1024\n' > "${WORK}/drop/conf.d/legacy.conf"
ring="$("${BIN}" --dump --root "${WORK}/drop" |
    awk '/^ring_capacity/ { print $3 }')"
[ "${ring}" = "8192" ] ||
    die "conf.d 叠加结果 ring_capacity=${ring}，期望 8192"
endgroup
say "conf.d 按文件名字典序叠加，无 NN- 前缀的文件被忽略"

# ─── 6. unit 契约 ────────────────────────────────────────────────────
group "config_d: unit 契约"
grep -qx 'Type=notify' "${UNIT}" || die "unit 未声明 Type=notify"
grep -qx 'ExecStart=/usr/sbin/config_d --root /etc/agentrt' "${UNIT}" ||
    die "unit 的 ExecStart 与配置根不一致"
grep -qx 'ExecReload=/bin/kill -HUP \$MAINPID' "${UNIT}" ||
    die "unit 未提供 ExecReload"
if grep -rqn '/etc/agentrt/daemons/' "${ROOT}/system/units"; then
    die "unit 仍引用旧的 /etc/agentrt/daemons/ 目录"
fi
endgroup
say "unit 契约成立"

say "config-d 门禁通过"
