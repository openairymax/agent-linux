#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_module_signature.sh — ci/gates/module-signature.sh 的分支回归
#
# 门禁判的是镜像树的模块签名尾部与运行内核污染位。真实树只在装机后才有
# 意义，判定逻辑只能由合成夹具驱动：这里按内核 scripts/sign-file 的追加
# 顺序造出「正文 + PKCS#7 + module_signature + 魔数」，再逐条破坏单一前提，
# 断言只看退出码与稳定判据名。
#
# 真实镜像是否合规不在这里判：流水线的 ci/ci.sh module-signature 负责那一
# 条，本文件只保证门禁没有漏判与误判。
#
# 用法: bash ci/tests/test_module_signature.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/module-signature.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
IMG="${tmp}/img"
REL="6.6.157-alk"
MOD="${IMG}/usr/lib/modules/${REL}/kernel/security/airy/airy.ko"
TAINT="${IMG}/proc/sys/kernel/tainted"

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${GATE}" "${FX}/ci/gates/module-signature.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/modsig.py" "${FX}/ci/lib/modsig.py"

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
    bash "${FX}/ci/gates/module-signature.sh" "$@" 2>&1
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

# sig <路径> <id_type> <sig_len> — 按 sign-file 的追加顺序落一份模块文件。
sig()
{
    python3 - "$1" "${2:-2}" "${3:-32}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
ident, sig_len = int(sys.argv[2]), int(sys.argv[3])
marker = b"~Module signature appended~\n"
body = b"\x7fELF\x02\x01\x01" + b"\x00" * 64
sig = b"\x30\x82" + b"\x2a" * 30
info = bytes([0, 4, ident, 0, 0, 0, 0, 0]) + sig_len.to_bytes(4, "big")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(body + sig + info + marker)
PY
}

taint()
{
    mkdir -p "$(dirname "${TAINT}")"
    printf '%s\n' "$1" >"${TAINT}"
}

mkfix()
{
    rm -rf "${IMG}"
    sig "${MOD}"
    taint 0
}

mkfix

out="$(run --root "${IMG}")"
check "合规镜像两项判定通过" 0 "$?" "${out}" "模块签名 1/1 通过"
case "${out}" in
*"内核污染位 0"*) ok "污染位判定输出" ;;
*) bad "污染位判定输出"; printf '%s\n' "${out}" >&2 ;;
esac

out="$(run --root "${IMG}" --kind struct)"
check "只看签名尾部通过" 0 "$?" "${out}" "模块签名"

out="$(run --root "${IMG}" --kind taint)"
check "只看污染位通过" 0 "$?" "${out}" "内核污染位"

out="$(run --root "${IMG}" --kernel "${REL}")"
check "指定内核版本通过" 0 "$?" "${out}" "模块签名"

out="$(run --root "${IMG}" --kernel 6.6.0-oe2403)"
check "内核版本目录不存在退 5" 5 "$?" "${out}" "没有模块"

out="$(run --root "${IMG}" --taint "${TAINT}")"
check "显式污染位文件通过" 0 "$?" "${out}" "内核污染位"

out="$(run --root "${tmp}/nope")"
check "镜像根不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run)"
check "缺少 --root 退 5" 5 "$?" "${out}" "--root 必填"

out="$(run --root "${IMG}" --kind elfx)"
check "未知 --kind 退 5" 5 "$?" "${out}" "只接受"

out="$(run --root "${IMG}" --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run --root "${IMG}" --kernel)"
check "参数缺值退 5" 5 "$?" "${out}" "需要参数值"

mkdir -p "${tmp}/empty"
out="$(run --root "${tmp}/empty")"
check "镜像根缺 usr/lib/modules 退 5" 5 "$?" "${out}" "不存在"

mv "${FX}/ci/lib/modsig.py" "${tmp}/modsig.py"
out="$(run --root "${IMG}")"
check "缺少校验器退 5" 5 "$?" "${out}" "缺少校验器"
mv "${tmp}/modsig.py" "${FX}/ci/lib/modsig.py"

mkfix
rm -f "${MOD}"
out="$(run --root "${IMG}")"
check "无模块退 5（不以空集放行）" 5 "$?" "${out}" "没有模块"

mkfix
printf '\x7fELF\n' >"${MOD}"
out="$(run --root "${IMG}")"
check "过短文件判未签名" 1 "$?" "${out}" "(short)"

mkfix
python3 - "${MOD}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
info = bytes([0, 4, 2, 0, 0, 0, 0, 0]) + (32).to_bytes(4, "big")
path.write_bytes(b"\x7fELF" + b"\x00" * 64 + b"\x30\x82" + b"\x2a" * 30 + info)
PY
out="$(run --root "${IMG}")"
check "缺尾部魔数判未签名" 1 "$?" "${out}" "(no-marker)"

mkfix
sig "${MOD}" 7
out="$(run --root "${IMG}")"
check "未知 id_type 判未签名" 1 "$?" "${out}" "(bad-id-type)"

mkfix
sig "${MOD}" 1
out="$(run --root "${IMG}")"
check "legacy 构型缺密钥标识判未签名" 1 "$?" "${out}" "(bad-note)"

mkfix
sig "${MOD}" 2 0
out="$(run --root "${IMG}")"
check "签名长度为零判未签名" 1 "$?" "${out}" "(zero-sig)"

mkfix
sig "${MOD}" 2 4096
out="$(run --root "${IMG}")"
check "签名段吞掉正文判未签名" 1 "$?" "${out}" "(sig-overrun)"

mkfix
sig "${IMG}/usr/lib/modules/${REL}/kernel/fs/ext4/ext4.ko"
python3 - "${MOD}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_bytes(b"\x7fELF" + b"\x00" * 64)
PY
out="$(run --root "${IMG}")"
check "多模块中一个未签名即判失败" 1 "$?" "${out}" "1/2"

mkfix
taint 8192
out="$(run --root "${IMG}" --kind taint)"
check "污染位 8192 判失败" 1 "$?" "${out}" "tainted=8192"

mkfix
taint 12289
out="$(run --root "${IMG}")"
check "污染位含 8192 位判失败" 1 "$?" "${out}" "tainted=12289"

mkfix
rm -f "${TAINT}"
out="$(run --root "${IMG}" --kind taint)"
check "污染位不可读退 5（不当零放行）" 5 "$?" "${out}" "不可读"

mkfix
taint "8192 "
out="$(run --root "${IMG}" --kind taint)"
check "污染位带空白仍按数值判" 1 "$?" "${out}" "tainted=8192"

mkfix
taint "tainted"
out="$(run --root "${IMG}" --kind taint)"
check "污染位非整数退 5" 5 "$?" "${out}" "不是十进制整数"

if [ "${fails}" -ne 0 ]; then
    printf 'test_module_signature: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_module_signature: 全部通过\n'
