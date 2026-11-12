#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_accel_contract.sh — 加速后端输出契约的判据回归
#
# 三份契约（行式、JSON、两种渲染同源）此前内嵌在 gate 的 heredoc 里，只能
# 靠造一台真机跑门禁来验。收进 ci/lib/accel_contract.py 之后可以用负例驱动：
# 每一类漂移各造一次，确认判负；对照组确认不误判。
#
# 断言只看退出码：文案要能改，判据不能改。用法类错误单列 2，与契约被违反
# 的 1 分开，避免流水线把「调用写错」当成「契约漂移」。
#
# 用法: bash ci/tests/test_accel_contract.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TOOL="${ROOT}/ci/lib/accel_contract.py"
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

# want RC DESC ARGS... —— 跑一次，只比退出码。
want()
{
    local rc=$1 desc=$2
    shift 2
    python3 "${TOOL}" "$@" >/dev/null 2>&1
    if [ "$?" -eq "${rc}" ]; then
        ok "${desc}"
    else
        bad "${desc}（期望 ${rc}）"
    fi
}

GOOD="cuda=present rocm=absent vulkan=present cpu=present"
# 契约 JSON 跨行写：比对的是解析后的结构，缩进只是给人看。
JSON='{"backends":[
{"backend":"cuda","present":true,"evidence":["ok:x"]},
{"backend":"rocm","present":false,"evidence":["ok:y"]},
{"backend":"vulkan","present":true,"evidence":["ok:z"]},
{"backend":"cpu","present":true,"evidence":["builtin:arch"]}]}'

want 0 '行式：四段齐全即通过' line "${GOOD}"
want 1 '行式：段数不足判负' line 'cuda=present rocm=present vulkan=present'
want 1 '行式：顺序漂移判负' \
    line 'rocm=present cuda=present vulkan=present cpu=present'
want 1 '行式：状态取值非法判负' \
    line 'cuda=maybe rocm=present vulkan=present cpu=present'
want 1 '行式：CPU 缺席判负' \
    line 'cuda=present rocm=present vulkan=present cpu=absent'

want 0 'JSON：四行齐全即通过' json "${JSON}"
want 1 'JSON：顶层多字段判负' json '{"backends":[],"x":1}'
want 1 'JSON：顺序漂移判负' \
    json '{"backends":[{"backend":"rocm","present":true,"evidence":["ok:a"]}]}'
want 1 'JSON：元素字段漂移判负' \
    json '{"backends":[{"backend":"cuda","present":true}]}'
want 1 'JSON：present 非布尔判负' \
    json '{"backends":[{"backend":"cuda","present":1,"evidence":["ok:a"]}]}'
want 1 'JSON：证据为空判负' \
    json '{"backends":[{"backend":"cuda","present":true,"evidence":[]}]}'
want 1 'JSON：证据前缀未知判负' json \
    '{"backends":[{"backend":"cuda","present":true,"evidence":["why:a"]}]}'
want 1 'JSON：证据缺前缀判负' \
    json '{"backends":[{"backend":"cuda","present":true,"evidence":["ok"]}]}'

want 0 '同源：两种渲染一致即通过' agree "${GOOD}" "${JSON}"
want 1 '同源：行式与 JSON 不一致判负' \
    agree 'cuda=absent rocm=present vulkan=present cpu=present' "${JSON}"
want 2 '用法：模式缺失按用法错误退出' agree "${GOOD}"

if [ "${fails}" -ne 0 ]; then
    printf '%d 项失败\n' "${fails}"
    exit 1
fi
printf '全部通过\n'
