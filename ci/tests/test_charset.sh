#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_charset.sh — ci/gates/charset.sh 的判据回归
#
# 钉住三层判据：数据层五个键与 UTF-8/GB18030 两条内容断言、载具层 spec 与
# kickstart 的装机校验、行为层合法转换与三类拒绝。负向逐条造坏：系统编码改
# GB18030、入站清单删 GB18030、spec 删入口、kickstart 删校验、配置/模块删件。
#
# 用法: bash ci/tests/test_charset.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/charset.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/services" "${FX}/system/tests/services" \
        "${FX}/distro/specs" "${FX}/distro/kickstart"
    cp "${ROOT}/ci/gates/charset.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/check.sh" "${FX}/ci/lib/"
    cp "${ROOT}/system/bin/airy-charset" "${FX}/system/bin/"
    chmod 0755 "${FX}/system/bin/airy-charset"
    cp -R "${ROOT}/system/services/airycharset" "${FX}/system/services/"
    cp -R "${ROOT}/system/services/airylib" "${FX}/system/services/"
    cp "${ROOT}/system/tests/services/test_charset_scan.py" \
        "${ROOT}/system/tests/services/test_charset_cli.py" \
        "${FX}/system/tests/services/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
    cp "${ROOT}/distro/kickstart/airymaxos-workstation.ks" \
        "${FX}/distro/kickstart/"
}

run()
{
    out="$(bash "${GATE}" "$@" 2>&1)"
    rc=$?
}

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

check()
{
    local name="$1" want="$2" needle="$3"

    if [ "${rc}" -ne "${want}" ]; then
        bad "${name}（rc=${rc}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *)
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        ;;
    esac
}

setup
run --tree "${FX}"
check "原树三层放行" 0 "编码面判定通过"

setup
sed -i 's/^CHARSET_ENC=.*/CHARSET_ENC=GB18030/' \
    "${FX}/system/services/airycharset/charset.conf"
run --tree "${FX}"
check "系统编码非 UTF-8 判负" 1 "系统编码不是 UTF-8"

setup
sed -i 's/^CHARSET_SOURCES=.*/CHARSET_SOURCES=GBK,BIG5/' \
    "${FX}/system/services/airycharset/charset.conf"
run --tree "${FX}"
check "入站清单缺 GB18030 判负" 1 "允许入站清单缺 GB18030"

setup
sed -i '/%{_bindir}\/airy-charset/d' \
    "${FX}/distro/specs/airymaxos-system.spec"
run --tree "${FX}"
check "spec 未装入口判负" 1 "spec 未安装 airy-charset 入口"

setup
sed -i '/airy-charset check/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
run --tree "${FX}"
check "装机段缺校验判负" 1 "workstation 未在装机段校验编码"

setup
rm "${FX}/system/services/airycharset/charset.conf"
run --tree "${FX}"
check "配置缺件判负" 1 "缺少 charset.conf"

setup
rm "${FX}/system/services/airylib/err.py"
run --tree "${FX}"
check "airylib 缺件判负" 1 "缺少 airylib.err"

setup
chmod -x "${FX}/system/bin/airy-charset"
run --tree "${FX}"
check "入口不可执行判负" 1 "airy-charset 不在位或不可执行"

setup
run --tree "${FX}" --bogus
check "未知参数退 5" 5 "未知参数"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
