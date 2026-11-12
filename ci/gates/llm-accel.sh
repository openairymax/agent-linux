#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/llm-accel.sh — 加速后端探测输出契约门禁
#
# 用法:
#   ci/gates/llm-accel.sh
#
# probe 的两种输出是运维与门禁共用的契约：行式给人看，JSON 给脚本读。
# 判定逻辑本身由单测覆盖，此处只钉「输出面」——字段集合、后端顺序、证据
# 前缀与两种渲染的一致性。这些一旦漂移，外部解析会静默失效：监控仍能取到
# 字符串，只是再也读不到它以为在读的字段。
#
# 三件事必须一起成立：
#
#   * 行式恒为四个「后端=状态」段，顺序 cuda、rocm、vulkan、cpu。
#   * JSON 顶层只有 backends，元素字段为 backend、present、evidence，
#     证据必须非空且前缀在固定集合内。
#   * 只读：probe 不读运行根，也不得写任何文件；只读探针若顺手落盘，
#     监控轮询就会把一次查询变成一次判定改写。
#
# 判定结果与真机显卡无关，故本门禁在无显卡的 runner 上同样成立：只断言
# 契约形状与两种渲染互相自洽，不断言某块卡必须命中。
#
# 退出码: 0 通过；1 检查失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

AIRY="${ROOT}/system/bin/airy-model"
PKG="${ROOT}/system/services/airyllm/accel"

TMP=""

usage()
{
    cat <<'EOF'
用法: ci/gates/llm-accel.sh
EOF
}

cleanup()
{
    [ -n "${TMP}" ] && rm -rf "${TMP}"
    return 0
}

trap cleanup EXIT

# 声明面 -------------------------------------------------------------------

check_entry()
{
    [ -x "${AIRY}" ] || die "入口不可执行: ${AIRY#"${ROOT}/"}"
    [ "$(sed -n '1p' "${AIRY}")" = '#!/usr/bin/env python3' ] ||
        die "入口首行不是 python3 shebang"
    say "入口可执行且带 shebang"
}

# 探测只有一份实现：包目录存在，且旧的单文件不得残留。两处并存时导入
# 顺序决定实际生效的那份，改了一处而不生效是最难排查的形态。
check_layout()
{
    local part
    [ -f "${PKG}/__init__.py" ] || die "缺少探测包: ${PKG#"${ROOT}/"}/__init__.py"
    for part in fs.py device.py icd.py probe.py; do
        [ -f "${PKG}/${part}" ] || die "探测包缺少 ${part}"
    done
    [ ! -e "${ROOT}/system/services/airyllm/accel.py" ] ||
        die "单文件 accel.py 与探测包并存"
    say "探测实现以单一包形态交付"
}

# 输出面 -------------------------------------------------------------------

# 三份输出契约的实现只在 ci/lib/accel_contract.py 一处（与 llm_contract.py
# 同形）。此处只把参数递过去：判据写在 Python 里才能单独喂用例，也不必让
# 读门禁的人在 shell 与 Python 之间来回切换上下文。
line_contract()
{
    python3 "${ROOT}/ci/lib/accel_contract.py" line "$1"
}

json_contract()
{
    python3 "${ROOT}/ci/lib/accel_contract.py" json "$1"
}

# 两种渲染必须同源：行式的每一段都是 JSON 里同一行的投影。分开维护就会
# 出现「监控看到 vulkan=present、脚本读到 present=false」这类自相矛盾。
agree()
{
    python3 "${ROOT}/ci/lib/accel_contract.py" agree "$1" "$2"
}

check_output()
{
    local line json

    line="$("${AIRY}" probe)" ||
        die "probe 行式输出退出码非 0"
    line_contract "${line}" || die "行式输出偏离契约: ${line}"
    json="$("${AIRY}" probe --json)" ||
        die "probe --json 退出码非 0"
    json_contract "${json}" || die "JSON 输出偏离契约: ${json}"
    agree "${line}" "${json}" || die "行式与 JSON 输出不一致"
    say "行式与 JSON 输出同源且符合契约"
}

check_readonly()
{
    local home="${TMP}/home" out

    mkdir -p "${home}"
    out="$("${AIRY}" --root "${home}" probe --json)" ||
        die "带运行时根调用 probe 退出码非 0"
    [ -n "${out}" ] || die "probe 无输出"
    [ -z "$(ls -A "${home}")" ] || die "probe 写入了运行时根: ${home}"
    say "probe 为只读探针，不落盘"
}

main()
{
    TMP="$(mktemp -d)" || die "无法创建临时目录"

    group "加速探测: 声明面"
    check_entry
    check_layout
    endgroup

    group "加速探测: 输出面"
    check_output
    check_readonly
    endgroup

    say "== llm-accel passed =="
}

case "${1:-}" in
    '' ) main ;;
    -h|--help) usage ;;
    *) err "未知参数: $1"; usage >&2; exit 1 ;;
esac
