#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_no_stub.sh — ci/gates/no-stub.sh 的分支回归
#
# 真实树上零命中是常态，判据只能靠负例驱动：逐条造出 todo!()、unimplemented!()、
# 产品代码的 unreachable!()、unsafe 与恒 Ok(()) 的函数，断言每条都判负；再造出
# 注释、字符串、测试上下文与 vendor/ 里的同名形态，断言不误判。
#
# 用法: bash ci/tests/test_no_stub.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/no-stub.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/ci/checks"
cp "${GATE}" "${FX}/ci/gates/no-stub.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/dirs.sh" "${FX}/ci/lib/dirs.sh"
cp "${ROOT}/ci/lib/gate.sh" "${FX}/ci/lib/gate.sh"
cp "${ROOT}/ci/lib/__init__.py" "${FX}/ci/lib/__init__.py"
cp "${ROOT}/ci/lib/rs_src.py" "${FX}/ci/lib/rs_src.py"
cp "${ROOT}/ci/lib/scan_src.py" "${FX}/ci/lib/scan_src.py"
cp "${ROOT}/ci/checks/no_stub.py" "${FX}/ci/checks/no_stub.py"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${FX}/ci/gates/no-stub.sh" "$@" 2>&1
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

fresh()
{
    rm -rf "${tmp}/src"
    mkdir -p "${tmp}/src"
}

fresh
cat >"${tmp}/src/lib.rs" <<'EOF'
//! 文档里提到 todo!()、unsafe 与 unreachable!() 都不算命中。

pub fn add(a: u8, b: u8) -> u8 {
    let note = "这个字符串里也有 todo!() 和 unsafe { }";
    println!("{note}");
    a + b
}

pub fn life<'a>(v: &'a str) -> usize {
    v.len()
}
EOF
out="$(run "${tmp}/src")"
check "合规树判通过" 0 "$?" "${out}" "0 issue(s)"

fresh
printf 'pub fn later() -> u8 {\n    todo!()\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "todo! 判拒绝" 1 "$?" "${out}" "S001"

fresh
printf 'pub fn later() -> u8 {\n    unimplemented!()\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "unimplemented! 判拒绝" 1 "$?" "${out}" "S002"

fresh
printf 'pub fn pick(v: Option<u8>) -> u8 {\n    match v {\n        Some(x) => x,\n        None => unreachable!(),\n    }\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "产品代码 unreachable! 判拒绝" 1 "$?" "${out}" "S003"

fresh
mkdir -p "${tmp}/src/tests"
printf 'fn pick(v: Option<u8>) -> u8 {\n    match v {\n        Some(x) => x,\n        None => unreachable!(),\n    }\n}\n' >"${tmp}/src/tests/it.rs"
out="$(run "${tmp}/src")"
check "tests 目录的 unreachable! 放行" 0 "$?" "${out}" "0 issue(s)"

fresh
cat >"${tmp}/src/a.rs" <<'EOF'
pub fn n() -> u8 {
    1
}

#[cfg(test)]
mod tests {
    fn pick(v: Option<u8>) -> u8 {
        match v {
            Some(x) => x,
            None => unreachable!(),
        }
    }
}
EOF
out="$(run "${tmp}/src")"
check "cfg(test) 模块的 unreachable! 放行" 0 "$?" "${out}" "0 issue(s)"

fresh
printf 'pub fn boom() {\n    unsafe { std::ptr::null::<u8>(); }\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "unsafe 判拒绝" 1 "$?" "${out}" "S004"

fresh
printf 'pub fn tick() -> Result<(), ()> {\n    Ok(())\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "恒 Ok(()) 判拒绝" 1 "$?" "${out}" "S005"

fresh
printf 'pub fn tick(n: u8) -> Result<(), u8> {\n    if n == 0 {\n        return Ok(());\n    }\n    Err(n)\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "有分支的 Ok(()) 放行" 0 "$?" "${out}" "0 issue(s)"

fresh
mkdir -p "${tmp}/src/vendor" "${tmp}/src/target"
printf 'pub fn later() -> u8 {\n    todo!()\n}\n' >"${tmp}/src/vendor/a.rs"
printf 'pub fn later() -> u8 {\n    todo!()\n}\n' >"${tmp}/src/target/a.rs"
out="$(run "${tmp}/src")"
check "vendor 与 target 不参与扫描" 0 "$?" "${out}" "0 issue(s)"

mkdir -p "${FX}/src"
printf 'pub fn add(a: u8, b: u8) -> u8 {\n    a + b\n}\n' >"${FX}/src/lib.rs"
out="$(cd "${FX}" && bash ci/gates/no-stub.sh src 2>&1)"
check "相对路径按仓库根解析" 0 "$?" "${out}" "0 issue(s)"

out="$(run "${tmp}/nope")"
check "目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run --help)"
check "帮助退 0" 0 "$?" "${out}" "用法"

mv "${FX}/ci/checks/no_stub.py" "${tmp}/no_stub.py"
out="$(run "${tmp}/src")"
check "缺少检查器退 5" 5 "$?" "${out}" "缺少检查器"
mv "${tmp}/no_stub.py" "${FX}/ci/checks/no_stub.py"

if [ "${fails}" -ne 0 ]; then
    printf 'test_no_stub: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_no_stub: 全部通过\n'
