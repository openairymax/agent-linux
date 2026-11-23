#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/charset.sh — 编码设置与转换面（I-30 / CA-092）
#
# 用法:
#   ci/gates/charset.sh [--tree DIR]
#
# 背景：编码此前只有 locale 一层，没有「系统编码、允许入站外源编码、BOM 与
# 非字符策略、计数单位」的可判定出处，也没有任何门禁能回答存量数据是否真为
# 合法 UTF-8。本门禁分三层取证：数据（默认配置与五个键）、载具（源包登记、
# 入口与站点包、装机段校验）、行为（判定与转换的成功、失败各一路）。
#
# 判定面：
#
#   * 工具包、配置、入口与单元测试都在位，入口可执行。
#   * 默认配置恰有五个键，系统编码为 UTF-8，允许入站清单含 GB18030。
#   * spec 登记 charset 与 airylib 源包、安装 %{_bindir}/airy-charset 与
#     %{python3_sitelib}/airycharset，并把默认配置装到
#     %{_sysconfdir}/agentrt/charset.conf。
#   * workstation 剖面在 %post 里以 `airy-charset check` 判负。
#   * 坏序列退 5、未知编码退 5、配置违规退 5，都不能被当成「已通过」。
#
# 退出码: 0 通过；1 检查失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/charset.sh [--tree DIR]
EOF
}

# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

gate_boot
opts_dir "$@"

PY="${TREE}/system/bin/airy-charset"
PKG="${TREE}/system/services/airycharset"
LIB="${TREE}/system/services/airylib"
CONF="${PKG}/charset.conf"
SPEC="${TREE}/distro/specs/airymaxos-system.spec"
KS="${TREE}/distro/kickstart/airymaxos-workstation.ks"

chk_files()
{
    say "数据与载具 ${TREE}"

    need_exe "${PY}" "airy-charset"
    need_file "${CONF}" "charset.conf"
    need_file "${PKG}/scan.py" "scan.py"
    need_file "${PKG}/conf.py" "conf.py"
    need_file "${PKG}/cli.py" "cli.py"
    need_file "${LIB}/err.py" "airylib.err"
    need_file "${LIB}/cli.py" "airylib.cli"
    need_file "${TREE}/system/tests/services/test_charset_scan.py" "编码单测"
    need_file "${TREE}/system/tests/services/test_charset_cli.py" "命令行单测"

    local keys
    keys=$(grep -cE '^[[:space:]]*CHARSET_[A-Z]+=' "${CONF}" 2>/dev/null)
    [ "${keys}" = "5" ] || bad "配置键 ${keys:-0} 条，应为 5"
    grep_re '^CHARSET_ENC=UTF-8$' "${CONF}" "系统编码不是 UTF-8"
    grep_in 'GB18030' "${CONF}" "允许入站清单缺 GB18030"
}

chk_spec()
{
    say "打包与剖面"

    need_file "${SPEC}" "airymaxos-system.spec" || return 0
    grep_in 'source-bundle ../../system/services/airycharset' "${SPEC}" \
        "spec 未登记 charset 源包"
    grep_in 'source-bundle ../../system/services/airylib' "${SPEC}" \
        "spec 未登记 airylib 源包"
    grep_in '%{python3_sitelib}/airycharset' "${SPEC}" \
        "spec 未安装 charset 站点包"
    grep_in '%{python3_sitelib}/airylib' "${SPEC}" \
        "spec 未安装 airylib 站点包"
    grep_in '%{_bindir}/airy-charset' "${SPEC}" "spec 未安装 airy-charset 入口"
    grep_in '%{_sysconfdir}/agentrt/charset.conf' "${SPEC}" \
        "spec 未安装 charset.conf"

    need_file "${KS}" "airymaxos-workstation.ks" || return 0
    grep_in 'airy-charset check' "${KS}" "workstation 未在装机段校验编码"
}

chk_pass()
{
    say "行为：合法输入与证据行"

    run_bin python3 -B "${PY}" show --conf "${CONF}"
    code_is 0 "show 退出码"
    out_has 'AIRYMAX-CHARSET-SHOW ok enc=UTF-8 sources=5' "show 未打印证据行"

    printf '中文ab\n' >"${TMP}/ok.txt"
    run_bin python3 -B "${PY}" check "${TMP}/ok.txt" --conf "${CONF}"
    code_is 0 "合法文件判定退出码"
    out_has 'AIRYMAX-CHARSET-CHECK ok files=1 cp=5' "合法文件未打印证据行"

    printf '\274\253\276\263\n' >"${TMP}/gb.txt"
    run_bin python3 -B "${PY}" convert --from GB18030 --in "${TMP}/gb.txt" \
        --out "${TMP}/utf.txt" --conf "${CONF}"
    code_is 0 "GB18030 转换退出码"
    out_has 'AIRYMAX-CHARSET-CONVERT ok' "转换未打印证据行"
    grep_in '极境' "${TMP}/utf.txt" "转换结果不是 UTF-8 极境"
}

chk_reject()
{
    say "行为：三类拒绝各判负一次"

    printf '\355\240\200' >"${TMP}/bad.txt"
    run_bin python3 -B "${PY}" check "${TMP}/bad.txt" --conf "${CONF}"
    code_is 5 "坏序列应退 5"

    run_bin python3 -B "${PY}" convert --from NOPE --in "${TMP}/ok.txt" \
        --out "${TMP}/x.txt" --conf "${CONF}"
    code_is 5 "未知编码应退 5"

    sed 's/^CHARSET_ENC=.*/CHARSET_ENC=GB18030/' "${CONF}" >"${TMP}/bad.conf"
    run_bin python3 -B "${PY}" show --conf "${TMP}/bad.conf"
    code_is 5 "配置违规应退 5"
}

TMP="$(mktemp -d)"

chk_files
chk_spec
chk_pass
chk_reject

if [ "${FAILS}" -ne 0 ]; then
    err "编码面判定失败 ${FAILS} 项"
    exit 1
fi

say "编码面判定通过"
exit 0
