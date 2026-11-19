#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_qemu_setup.sh — ci/gates/qemu-setup.sh 的复判契约回归
#
# 用例来自 run 44517530359：验收镜像的 qemu 装了三轮，三轮都败在
# security.ubuntu.com 上 ipxe-qemu 的 503，门禁没看返回值也没复判命令是否
# 存在，继续打印「固件就绪 / KVM 可用」并以 0 退出。三条装机腿随后在 0.03
# 秒内以 rc=77 跳过，失败点被推迟 20 分钟且指向了 guest。
#
# 因此这里钉三条契约，桩只回答 apt 的成败、不碰真实系统：
#   1. 装不上就判负，并指名缺哪一条命令；
#   2. 首轮失败要换索引再试一轮，第二轮装上了就放行；
#   3. 命令已在时不装（apt 一次都不调）。
#
# PATH 收窄到只有桩与必需工具：宿主机若真装了 qemu，第三个用例就失去了
# 意义。sudo 桩对 `-n chmod` 一律拒绝，让 KVM 分支走确定的降级路径。
#
# 用法: bash ci/tests/test_qemu_setup.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
QBIN="${tmp}/qbin"
STUB_LOG="${tmp}/calls"
STUB_NINST="${tmp}/installs"
export STUB_LOG STUB_NINST QBIN
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${tmp}/bin" "${QBIN}"
cp "${ROOT}/ci/gates/qemu-setup.sh" "${FX}/ci/gates/qemu-setup.sh"
cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/host.sh" "${ROOT}/ci/lib/apt.sh" \
    "${FX}/ci/lib/"
: >"${STUB_LOG}"
: >"${STUB_NINST}"

# 收窄后的 PATH：只放门禁真正用到的那几条命令，其余一律不可见。
for c in bash env dirname id timeout head chmod wc mkdir cat rm; do
    ln -s "$(command -v "${c}")" "${tmp}/bin/${c}"
done

# apt 桩：子命令由参数里的 install/update 认（apt.sh 先把 -o 选项排在前面，
# 不能按 $1 判）。update 一律成功；install 前 STUB_FAIL 次失败，之后把两条
# 命令装进 QBIN（QBIN 已在 PATH 上，装完即被 command -v 看见）。
cat >"${tmp}/bin/apt-get" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >>"${STUB_LOG}"
sub=""
for a in "$@"; do
    case "${a}" in
        install|update) sub="${a}"; break ;;
    esac
done
[ "${sub}" = install ] || exit 0
n=$(( $(wc -l <"${STUB_NINST}") + 1 ))
printf '%s\n' "${n}" >>"${STUB_NINST}"
[ "${n}" -le "${STUB_FAIL}" ] && exit 100
for c in qemu-system-x86_64 qemu-img; do
    printf '#!/bin/bash\necho "%s 9.9 stub"\n' "${c}" >"${QBIN}/${c}"
    chmod +x "${QBIN}/${c}"
done
exit 0
EOF

cat >"${tmp}/bin/sudo" <<'EOF'
#!/bin/bash
[ "$1" = -n ] && exit 1
exec "$@"
EOF
chmod +x "${tmp}/bin/apt-get" "${tmp}/bin/sudo"

GPATH="${tmp}/bin:${QBIN}"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# run FAILS —— 按「前 FAILS 次 install 失败」跑门禁一次。
run()
{
    : >"${STUB_LOG}"
    : >"${STUB_NINST}"
    STUB_FAIL="$1" PATH="${GPATH}" bash "${FX}/ci/gates/qemu-setup.sh" \
        --firmware bios 2>&1
}

# nfail SUB —— 桩收到的 SUB 子命令次数（参数里的单词，不按位置）。
nfail()
{
    awk -v p="$1" '{
        for (i = 1; i <= NF; i++) if ($i == p) n++
    } END { print n + 0 }' "${STUB_LOG}"
}

want_rc()
{
    if [ "$2" = "$3" ]; then
        ok "$1"
    else
        bad "$1（rc=$2，期望 $3）"
    fi
}

has()
{
    case "$2" in
    *"$3"*) ok "$1" ;;
    *)
        bad "$1（输出缺「$3」）"
        printf '%s\n' "$2" >&2
        ;;
    esac
}

lacks()
{
    case "$2" in
    *"$3"*) bad "$1（输出不该含「$3」）" ;;
    *) ok "$1" ;;
    esac
}

out="$(run 6)"
rc=$?
want_rc "装不上就判负" "${rc}" 1
has "判负要指名缺哪条" "${out}" "qemu-system-x86_64 qemu-img"
lacks "装不上时不得宣称就绪" "${out}" "qemu 已就绪"
[ "$(nfail install)" = 6 ] && ok "两轮各三次尝试" ||
    bad "两轮各三次尝试（install=$(nfail install)）"
[ "$(nfail update)" = 2 ] && ok "每轮重取索引" ||
    bad "每轮重取索引（update=$(nfail update)）"

out="$(run 3)"
rc=$?
want_rc "首轮失败后换索引重试" "${rc}" 0
has "首轮失败要换索引" "${out}" "换索引后再试一轮"
has "第二轮装上即放行" "${out}" "qemu 已就绪"
[ "$(nfail install)" = 4 ] && ok "第二轮一次成功" ||
    bad "第二轮一次成功（install=$(nfail install)）"

: >"${STUB_LOG}"
: >"${STUB_NINST}"
for c in qemu-system-x86_64 qemu-img; do
    printf '#!/bin/bash\necho "%s 9.9 stub"\n' "${c}" >"${QBIN}/${c}"
    chmod +x "${QBIN}/${c}"
done
out="$(PATH="${GPATH}" bash "${FX}/ci/gates/qemu-setup.sh" --firmware bios 2>&1)"
rc=$?
want_rc "已在则不装" "${rc}" 0
has "已在时直接报就绪" "${out}" "qemu 已就绪"
[ "$(nfail install)" = 0 ] && ok "已在则不调 apt" ||
    bad "已在则不调 apt（install=$(nfail install)）"

if [ "${fails}" -gt 0 ]; then
    printf '%d 项断言失败\n' "${fails}" >&2
    exit 1
fi
printf '全部通过\n'
