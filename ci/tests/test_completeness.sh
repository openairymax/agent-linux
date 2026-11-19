#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_completeness.sh — 归属册门禁的判定分支回归
#
# 归属册本体在 docs-closed，只在开发机上存在，流水线里看不到它。门禁的
# 判定逻辑因此必须自带夹具：一棵假的源码树、一棵假的设计树、一份包含
# 三态的册子，以及逐条破坏它的写法——除了「通过」这一条，每条判据都要有
# 一个能被破坏的用例，否则判红只是恰好。
#
# 夹具路径刻意复刻真实布局：<tmp>/docs/docs-closed/agent-linux-dev/
# 10-references/audit.md，让门禁从册子位置推导设计树的逻辑也被覆盖。
# ===========================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "${HERE}/../.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

FX="${TMP}/fx"
DOC="${TMP}/docs/docs-closed/agent-linux-dev/10-references/completeness-audit.md"
ANCHOR="${TMP}/docs/docs-linux/20-modules/01-kernel.md"
ok=0
fail=0

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/x" \
         "$(dirname "${ANCHOR}")" "$(dirname "${DOC}")"
cp "${ROOT}/ci/gates/completeness.sh" "${FX}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/completeness.py" "${FX}/ci/lib/"
: >"${ANCHOR}"
printf '# demo\n# 用法: demo\n' >"${FX}/ci/gates/demo.sh"
printf '# nohead\n' >"${FX}/ci/gates/nohead.sh"

run_gate()
{
    bash "${FX}/ci/gates/completeness.sh" "$@"
}

reset()
{
    cat >"${DOC}" <<'EOF'
# 功能归属册

## 3. 统计

| 归属 | 条目数 |
|---|---|
| 已实现 | 1 |
| 0.0.5 | 1 |
| 移出（承接版本） | 1 |
| **合计** | **3** |

## 4. 明细

| 编号 | 功能面 | 锚点 | 归属 | 证据 |
|---|---|---|---|---|
| CA-001 | a | `20-modules/01-kernel.md` | 已实现 | `system/x/`、`ci/gates/demo.sh` |
| CA-002 | b | `20-modules/01-kernel.md` | 0.0.5 | `system/x/` |
| CA-003 | c | `20-modules/01-kernel.md` | 移出（0.1.x） | — |
EOF
}

check()
{
    local desc="$1" want="$2" got out

    shift 2
    out="$("$@" 2>&1)"
    got=$?
    if [ "${got}" -eq "${want}" ]; then
        ok=$(( ok + 1 ))
        printf 'ok   %s\n' "${desc}"
    else
        fail=$(( fail + 1 ))
        printf 'FAIL %s（rc=%d，期望 %d）\n%s\n' "${desc}" "${got}" "${want}" "${out}"
    fi
}

has()
{
    local desc="$1" pat="$2" out

    shift 2
    out="$("$@" 2>&1)"
    if printf '%s' "${out}" | grep -q -- "${pat}"; then
        ok=$(( ok + 1 ))
        printf 'ok   %s\n' "${desc}"
    else
        fail=$(( fail + 1 ))
        printf 'FAIL %s（输出中未见「%s」）\n%s\n' "${desc}" "${pat}" "${out}"
    fi
}

reset
check '三态齐全时通过' 0 run_gate
has   '计数与明细一致' 'rows=3 impl=1 open=1 out=1' run_gate
has   'JSON 输出可读' '"open": 1' run_gate --json
check '发布模式拒绝 0.0.5' 1 run_gate --release
has   '发布模式点名占用条目' '发布前仍有 1 条 0.0.5：CA-002' run_gate --release

reset
sed -i 's/| 0.0.5 |/| 待定 |/' "${DOC}"
check '归属不在三态内判红' 1 run_gate
has   '说明哪一列坏了' '归属列不在三态之内' run_gate

reset
sed -i '/^| CA-002 /p' "${DOC}"
check '编号重复判红' 1 run_gate
has   '点名重复编号' '编号重复: CA-002' run_gate

reset
sed -i 's/| \*\*合计\*\* | \*\*3\*\* |/| **合计** | **4** |/' "${DOC}"
check '统计与明细不一致判红' 1 run_gate
has   '给出两侧数字' '合计 表 4，明细 3' run_gate

reset
sed -i '/| 移出（承接版本） | 1 |/d' "${DOC}"
check '统计缺行判红' 1 run_gate
has   '点名缺的行' '缺少「移出（承接版本）」行' run_gate

reset
sed -i 's|`system/x/`|`system/nope/`|g' "${DOC}"
check '证据缺件判红' 1 run_gate
has   '点名缺的路径' '缺 system/nope/' run_gate

reset
sed -i 's|`ci/gates/demo.sh`|`ci/gates/nohead.sh`|' "${DOC}"
check '门禁缺用法头判红' 1 run_gate
has   '点名缺头的门禁' '缺 # 用法: 头' run_gate

reset
sed -i 's|`ci/gates/demo.sh`|`demo.sh`|' "${DOC}"
check '裸脚本名判红' 1 run_gate
has   '要求写相对路径' '证据须写相对路径（demo.sh）' run_gate

reset
sed -i 's|20-modules/01-kernel.md|20-modules/09-nope.md|g' "${DOC}"
check '锚点缺件判红' 1 run_gate
has   '点名缺的锚点' '锚点缺 20-modules/09-nope.md' run_gate

reset
sed -i 's|移出（0.1.x）|移出（）|' "${DOC}"
check '移出无承接版本判红' 1 run_gate
has   '按未归属处理' '归属列不在三态之内' run_gate

reset
printf '| CA-004 | d | `x` | 已实现 |\n' >>"${DOC}"
check '列数不足判红' 1 run_gate
has   '点名行列数' '不是五列' run_gate

reset
mv "${DOC}" "${DOC}.off"
check '册子缺席时跳过' 0 run_gate
has   '跳过时留 note' '本门禁跳过' run_gate
mv "${DOC}.off" "${DOC}"

check '--doc 指错退 5' 5 run_gate --doc "${TMP}/nope.md"
check '未知参数退 5' 5 run_gate --bogus
check '帮助退 0' 0 run_gate --help
has   '帮助含用法行' '用法: ci/ci.sh completeness' run_gate --help

printf '\n'
if [ "${fail}" -ne 0 ]; then
    printf 'test_completeness: %d 项失败（共 %d 项）\n' "${fail}" "$(( ok + fail ))" >&2
    exit 1
fi
printf 'test_completeness: %d 项通过\n' "${ok}"
