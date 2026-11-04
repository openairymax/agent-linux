#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/backlight.sh — 屏幕与键盘背光用户态入口门禁（I-25 / HW-03）
#
# 用法:
#   ci/gates/backlight.sh [--tree DIR]
#
# 背景：0.0.3 没有任何背光入口，换机型或接外接屏后只能靠外部工具猜设备
# 名，猜错就把亮度写到一条不生效的通路上。0.0.4 由 `airy-bright` 统一处理
# 两条通路：屏幕走 `/sys/class/backlight`，键盘走 `/sys/class/leds` 里名字
# 含 `kbd_backlight` 的那一个。
#
# 声明面：
#
#   * 包内模块齐全，入口可执行，帮助里能看到四个子命令。
#   * 包内没有桩标记——「可读写」不能靠 TODO 占位。
#   * 代码里不出现具体机型名：机型名只允许出现在注释里，实现按类型与
#     上限排序，换机器不需要改代码。
#
# 实物面（造一棵临时 sysfs 树，全部判定与真机内核无关）：
#
#   * firmware 型与 raw 型同时存在时选 firmware：raw 那一路在部分机型
#     写不生效，报告里两块仍逐条列出。
#   * 写 50% 落一半刻度；默认下限 1% 时写 0% 落到最低一格；显式
#     `--floor 0` 才允许真的关屏；键盘不受屏幕下限保护。
#   * 键盘缺席时整体判 partial 且退出码 3，屏幕照样落地。
#   * 空树判 absent 且退出码 4——KMS 尚未接管与「调好了」必须分得开。
#   * brightness 节点消失时判 failed 且错误为 gone，不写不猜。
#
# 退出码: 0 通过；1 检查失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly MODULES="__init__.py cli.py config.py device.py level.py plan.py policy.py report.py"
readonly MARK="AIRYMAX-BRIGHT"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
TMP=""
FAILS=0

usage()
{
    cat <<'EOF'
用法: ci/gates/backlight.sh [--tree DIR]
EOF
}

cleanup()
{
    [ -n "${TMP}" ] && rm -rf "${TMP}"
    return 0
}

trap cleanup EXIT

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tree)
            [ -n "${2:-}" ] || { err "--tree 需要目录路径"; exit "${EINVAL}"; }
            [ -d "$2" ] || { err "--tree 目录不存在: $2"; exit "${EINVAL}"; }
            TREE="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TREE}" ] || TREE="${ROOT}"

bad() { err "$*"; FAILS=$((FAILS + 1)); }

# 声明面 -------------------------------------------------------------------

chk_pkg()
{
    local dir="$1/system/services/airybright" m missing=0
    local bin="$1/system/bin/airy-bright"

    say "声明面 ${1}"
    [ -d "${dir}" ] || { bad "package: 缺少 services/airybright"; return 0; }
    for m in ${MODULES}; do
        [ -f "${dir}/${m}" ] || { bad "package: 缺少 ${m}"; missing=1; }
    done
    [ -x "${bin}" ] || bad "entry: system/bin/airy-bright 不可执行"
    [ -x "${bin}" ] || return 0
    [ "$(sed -n '1p' "${bin}")" = '#!/usr/bin/env python3' ] ||
        bad "entry: 首行不是 python3 shebang"
    for m in status set step config; do
        "${bin}" --help 2>/dev/null | grep -qE "(^|[[:space:]])${m}([[:space:]]|$)" ||
            bad "entry: 帮助里没有 ${m} 子命令"
    done
    [ "${missing}" -eq 0 ] || return 0

    # 桩标记与机型硬编码：注释里的机型说明是取证记录，不算硬编码。
    if grep -nE 'TODO|FIXME|NotImplementedError' "${dir}"/*.py; then
        bad "package: 存在桩标记"
    fi
    if grep -nE 'intel_backlight|tpacpi|platform::|amdgpu_bl' "${dir}"/*.py |
        grep -vE '^[^:]+:[0-9]+:[[:space:]]*#'; then
        bad "package: 代码里硬编码了机型设备名"
    fi
}

# 实物面 -------------------------------------------------------------------

# 只取标准输出：告警与回溯留在终端，不污染要解析的状态行。
json_get()
{
    python3 -c 'import json,sys
d = json.load(sys.stdin)
print(eval(sys.argv[1], {"d": d}))' "$1"
}

# node DIR NAME MAX RAW [TYPE] —— 铺一块屏幕面板（有 type 节点）。
node()
{
    local dir="$1" name="$2" max="$3" raw="$4" kind="${5:-}"
    mkdir -p "${dir}/${name}"
    printf '%s\n' "${max}" >"${dir}/${name}/max_brightness"
    printf '%s\n' "${raw}" >"${dir}/${name}/brightness"
    [ -z "${kind}" ] || printf '%s\n' "${kind}" >"${dir}/${name}/type"
}

# led DIR NAME MAX RAW —— 铺一条 led；名字含 kbd_backlight 的才算键盘背光。
led()
{
    node "$1" "$2" "$3" "$4"
}

level_of() { sed -n '1p' "$1"; }

# run ARGS... —— 执行一次 airy-bright，标准输出留给调用方。
run()
{
    "${BIN}" "$@" 2>/dev/null
}

code_of()
{
    "${BIN}" "$@" >/dev/null 2>&1
    printf '%s' "$?"
}

chk_tree()
{
    local bl="${TMP}/backlight" leds="${TMP}/leds" out code

    say "实物面（临时 sysfs 树）"
    mkdir -p "${bl}" "${leds}"
    node "${bl}" intel_backlight 1000 400 firmware
    node "${bl}" panel0 937 468 raw
    led "${leds}" platform::kbd_backlight 3 2
    led "${leds}" input0::capslock 1 0

    # 1) 只读：firmware 优先，两块面板都在报告里。
    code=$(code_of status --json --backlight "${bl}" --leds "${leds}")
    out=$(run status --json --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 0 ] || bad "status: 退出码 ${code}，应为 0"
    [ "$(printf '%s' "${out}" | json_get 'd["state"]')" = ok ] ||
        bad "status: state 不是 ok"
    [ "$(printf '%s' "${out}" | json_get 'd["mark"]')" = "${MARK}" ] ||
        bad "status: mark 不是 ${MARK}"
    [ "$(printf '%s' "${out}" | json_get 'len(d["devices"])')" -eq 2 ] ||
        bad "status: 设备行数不是 2"
    [ "$(printf '%s' "${out}" | json_get 'd["devices"][0]["name"]')" = \
        intel_backlight ] || bad "status: 首行不是 firmware 型的 intel_backlight"

    # 2) 写 50%：1000 刻度的半分是 500。
    code=$(code_of set screen 50 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 0 ] || bad "set: 写 50% 退出码 ${code}"
    [ "$(level_of "${bl}/intel_backlight/brightness")" = 500 ] ||
        bad "set: 刻度不是 500"

    # 3) 默认下限 1%：写 0% 落到最低一格，不黑屏。
    code=$(code_of set screen 0 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 0 ] || bad "floor: 写 0% 退出码 ${code}"
    [ "$(level_of "${bl}/intel_backlight/brightness")" = 10 ] ||
        bad "floor: 默认下限未生效"

    # 4) 显式 --floor 0 才允许真的关屏。
    code=$(code_of set screen 0 --floor 0 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 0 ] || bad "open-floor: 退出码 ${code}"
    [ "$(level_of "${bl}/intel_backlight/brightness")" = 0 ] ||
        bad "open-floor: 未真正关屏"

    # 5) 键盘不受屏幕下限保护：关掉键盘背光是合法用法。
    code=$(code_of set keyboard 0 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 0 ] || bad "keyboard: 退出码 ${code}"
    [ "$(level_of "${leds}/platform::kbd_backlight/brightness")" = 0 ] ||
        bad "keyboard: 未落到 0"

    # 6) 相对调节以自身读数为基准：40% 走 +10 应到 50%。
    code_of set screen 40 --backlight "${bl}" --leds "${leds}" >/dev/null
    code=$(code_of step screen 10 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 0 ] || bad "step: 退出码 ${code}"
    [ "$(level_of "${bl}/intel_backlight/brightness")" = 500 ] ||
        bad "step: 刻度不是 500"

    # 7) 键盘缺席：整体 partial（3），屏幕照样落地。
    mv "${leds}/platform::kbd_backlight" "${TMP}/kbd-off"
    code=$(code_of set all 50 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 3 ] || bad "partial: 退出码 ${code}，应为 3"
    [ "$(level_of "${bl}/intel_backlight/brightness")" = 500 ] ||
        bad "partial: 屏幕未落地"
    mv "${TMP}/kbd-off" "${leds}/platform::kbd_backlight"

    # 8) 空树：absent（4），与「调好了」分开。
    mkdir -p "${TMP}/empty-bl" "${TMP}/empty-led"
    code=$(code_of status --backlight "${TMP}/empty-bl" \
        --leds "${TMP}/empty-led")
    [ "${code}" -eq 4 ] || bad "absent: 退出码 ${code}，应为 4"

    # 9) brightness 消失：failed（1）且错误为 gone，不写不猜。
    rm -f "${bl}/intel_backlight/brightness"
    out=$(run set screen 50 --json --backlight "${bl}" --leds "${leds}")
    code=$(code_of set screen 50 --backlight "${bl}" --leds "${leds}")
    [ "${code}" -eq 1 ] || bad "gone: 退出码 ${code}，应为 1"
    [ "$(printf '%s' "${out}" | json_get 'd["devices"][0]["error"]')" = gone ] ||
        bad "gone: 错误字段不是 gone"
}

need_cmd python3 || exit 1
TMP="$(mktemp -d)"

chk_pkg "${TREE}"
BIN="${TREE}/system/bin/airy-bright"
[ -x "${BIN}" ] && chk_tree

if [ "${FAILS}" -ne 0 ]; then
    err "背光入口失败 ${FAILS} 项"
    exit 1
fi

say "背光入口通过"
exit 0
