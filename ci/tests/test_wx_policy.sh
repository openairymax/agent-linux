#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_wx_policy.sh — ci/gates/wx-policy.sh 的分支回归
#
# 门禁有两类判定：声明面看文件与禁用手段，实物面看回归器在真内核上的结论。
# 实物面只有装机后的机器能判，这里用三种手段把它覆盖掉：
#
#   * 手工合成的最小 ELF 直接喂给 --scan，断言 PT_GNU_STACK 的执行位解析
#     既不会漏判也不会误判（不依赖 gcc，也不依赖宿主真实库）。
#   * 把回归器换成分支桩，逐条断言门禁对 0 / 1 / 4 / 其他退出码的处置。
#   * gcc 可用时再跑一次真实回归器，断言结论与本机是否 AirymaxOS 内核一致；
#     这条断言防的是「工具在通用机器上假装通过」。
#
# 用法: bash ci/tests/test_wx_policy.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/wx-policy.sh"
TOOL="${ROOT}/ci/tools/execstack_regress.py"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/ci/tools" \
    "${FX}/distro" "${FX}/system"
cp "${GATE}" "${FX}/ci/gates/wx-policy.sh"
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

run()
{
    bash "${FX}/ci/gates/wx-policy.sh" "$@" 2>&1
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

tool()
{
    cp "${TOOL}" "${FX}/ci/tools/execstack_regress.py"
    chmod +x "${FX}/ci/tools/execstack_regress.py"
}

# stub CODE —— 分支桩：--scan 恒成功，实物面按 CODE 收场。四个结论标记与
# PT_GNU_STACK 都在文件里，声明面因此照常通过，测的只是实物面的处置。
stub()
{
    cat >"${FX}/ci/tools/execstack_regress.py" <<EOF
#!/usr/bin/env python3
# AIRY-WX-PASS AIRY-WX-FAIL AIRY-WX-WAIVED PT_GNU_STACK
import sys
PT_GNU_STACK = 0x6474E551


def shape(p_type):
    """与真工具同形的程序头判定；本桩不解析，只为满足声明面的形状检查。"""
    return p_type == PT_GNU_STACK


tag = {0: "AIRY-WX-PASS", 1: "AIRY-WX-FAIL", 4: "AIRY-WX-WAIVED"}.get($1, "AIRY-WX-ODD")
if "--scan" in sys.argv:
    print("RWE  桩  RWE" if $1 == 1 else "SCAN 桩 rwe=0")
    sys.exit(0)
print(tag)
sys.exit($1)
EOF
    chmod +x "${FX}/ci/tools/execstack_regress.py"
}

tool
out="$(run)"
check "合规树判定通过" 0 "$?" "${out}" "W^X 门禁通过"

chmod -x "${FX}/ci/tools/execstack_regress.py"
out="$(run)"
check "回归器不可执行判拒绝" 1 "$?" "${out}" "回归器不可执行"

tool
printf '# TODO\n' >>"${FX}/ci/tools/execstack_regress.py"
out="$(run)"
check "回归器带桩标记判拒绝" 1 "$?" "${out}" "回归器存在桩标记"

tool
sed -i '/AIRY-WX-WAIVED/d' "${FX}/ci/tools/execstack_regress.py"
out="$(run)"
check "回归器缺结论标记判拒绝" 1 "$?" "${out}" "缺少 AIRY-WX-WAIVED"

tool
sed -i 's/p_type == PT_GNU_STACK/p_type == 0/' \
    "${FX}/ci/tools/execstack_regress.py"
out="$(run)"
check "回归器不解析程序头判拒绝" 1 "$?" "${out}" "缺少 PT_GNU_STACK 解析"

tool
rm -f "${FX}/ci/tools/execstack_regress.py"
out="$(run)"
check "回归器缺失判拒绝" 1 "$?" "${out}" "缺少 ci/tools/execstack_regress.py"

tool
printf 'execstack -c libfoo.so\n' >"${FX}/distro/pack.sh"
out="$(run)"
check "交付链清位判拒绝" 1 "$?" "${out}" "W^X 绕过手段"

printf 'gcc -Wl,-z execstack -o a.so a.c\n' >"${FX}/system/build.sh"
out="$(run)"
check "交付链重编可执行栈判拒绝" 1 "$?" "${out}" "W^X 绕过手段"

rm -f "${FX}/distro/pack.sh" "${FX}/system/build.sh"
printf '# 说明：历史上的 execstack -c 只用于取证\n' >"${FX}/distro/note.sh"
out="$(run)"
check "注释里的历史说明不判拒绝" 0 "$?" "${out}" "W^X 门禁通过"
rm -f "${FX}/distro/note.sh"

stub 0
out="$(run --live)"
check "实物面通过时判通过" 0 "$?" "${out}" "W^X 判据成立"

stub 1
out="$(run --live)"
check "实物面失败时判失败" 1 "$?" "${out}" "W^X 判据不成立"

stub 4
out="$(run --live)"
check "本机不适用按环境不满足" 5 "$?" "${out}" "不适用"

stub 7
out="$(run --live)"
check "回归器异常退出码按环境不满足" 5 "$?" "${out}" "回归器退出码 7"

# --- ELF 扫描面：手工合成两个只有程序头差别的 ELF64 ------------------------

mkdir -p "${tmp}/elf"
python3 - "${tmp}/elf" <<'EOF'
import struct
import sys

out = sys.argv[1]


def elf(path, flags):
    ident = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9
    head = ident + struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64,
                               56, 1, 0, 0, 0)
    phdr = struct.pack("<IIQQQQQQ", 0x6474E551, flags, 0, 0, 0, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(head + phdr)


elf(out + "/plain.so", 6)
elf(out + "/xstack.so", 7)
EOF
out=$(python3 "${TOOL}" --scan "${tmp}/elf" --no-probe 2>&1)
check "扫描器判出可执行栈库" 0 "$?" "${out}" "xstack.so"
case "${out}" in
*"RWE  ${tmp}/elf/plain.so"*) bad "扫描器把普通库判成可执行栈" ;;
*) ok "扫描器未误判普通库" ;;
esac

# --- 真实回归器：结论必须与「本机是不是 AirymaxOS 内核」一致 ----------------

if command -v gcc >/dev/null 2>&1; then
    out=$(python3 "${TOOL}" --quiet 2>&1)
    rc=$?
    if [ -d /sys/kernel/security/airy ]; then
        check "真机回归器判成立" 0 "${rc}" "${out}" "AIRY-WX-PASS"
    else
        check "通用机器上判不适用" 4 "${rc}" "${out}" "AIRY-WX-WAIVED"
    fi
else
    printf 'note 跳过真实回归器：无 gcc\n'
fi

out="$(run --tree "${tmp}/absent")"
check "tree 目录不存在按参数错误" 5 "$?" "${out}" "目录不存在"

out="$(run --root "${tmp}/absent")"
check "root 目录不存在按参数错误" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数按参数错误" 5 "$?" "${out}" "未知参数"

out="$(run --help)"
check "帮助以 0 退出" 0 "$?" "${out}" "用法"

exit "${fails}"
