#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_disk_layout.sh — ci/gates/disk-layout.sh 的分支回归
#
# runner 上没有受测机器，门禁的常规入口是重放快照。这里用手工构造的
# status 快照与 fstab 驱动全部分支：双盘与单盘两种合规形态、缺 /data、
# /data 非末位分区、快照本身不可读，以及 fstab 用 UUID（持久）与按设备
# 名写源（扩容改号后失挂）两例。断言只看退出码与稳定 token，不看措辞。
#
# 用法: bash ci/tests/test_disk_layout.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/disk-layout.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
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

# part NAME FS MOUNT ROLE —— MOUNT 传 JSON 值（"..." 或 null）。
part()
{
    printf '{"name":"%s","size":1048576,"fs":"%s","mount":%s,"role":"%s"}' \
        "$1" "$2" "$3" "$4"
}

disk()
{
    printf '{"name":"%s","size":1073741824,"bus":"%s","table":"gpt","parts":[%s]}' \
        "$1" "$2" "$3"
}

snap()
{
    printf '{"schema":1,"kind":"%s","disks":[%s]}\n' "$2" "$3" >"$1"
}

run()
{
    bash "${GATE}" "$@" 2>&1
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

# 分区名必须与所属盘一致：判定按内核名末尾的分区号排定次序，单盘里塞
# 进别的盘的分区名，排序与拓扑自相矛盾，测的就不是真实装机形态了。
root="$(part nvme0n1p2 btrfs '"/"' system)"
data="$(part sdc1 xfs '"/data"' data)"
sroot="$(part sda1 btrfs '"/"' system)"
sdata="$(part sda2 xfs '"/data"' data)"
sswap="$(part sda3 swap null swap)"

snap "${tmp}/dual.json" multi \
    "$(disk nvme0n1 nvme "${root}"),$(disk sdc sata "${data}")"
out="$(run --snapshot "${tmp}/dual.json")"
check "双盘：/ 与 /data 各自独立" 0 "$?" "${out}" '"verdict": "ok"'

snap "${tmp}/single.json" single "$(disk sda sata "${sroot},${sdata}")"
out="$(run --snapshot "${tmp}/single.json")"
check "单盘：同盘切分且 /data 末位" 0 "$?" "${out}" '"verdict": "ok"'
case "${out}" in
*'"kind": "single"'*) ok "单盘 token 正确" ;;
*) bad "单盘 token 不正确"; printf '%s\n' "${out}" >&2 ;;
esac

snap "${tmp}/bare.json" single "$(disk sda sata "${sroot}")"
out="$(run --snapshot "${tmp}/bare.json")"
check "缺 /data 判拒绝" 1 "$?" "${out}" '"verdict": "no-data"'

# 两位数分区号：文本顺序把 sda10 排在 sda2 之前，判定必须先按号归一，
# 否则末号分区被当成 sda2，真实的 /data（sda10）反而判 not-last。
snap "${tmp}/ten.json" single \
    "$(disk sda sata "$(part sda10 xfs '"/data"' data),$(part sda2 btrfs '"/"' system)")"
out="$(run --snapshot "${tmp}/ten.json")"
check "分区号两位数按号归一后 /data 仍判末位" 0 "$?" "${out}" '"verdict": "ok"'

snap "${tmp}/tail.json" single "$(disk sda sata "${sroot},${sdata},${sswap}")"
out="$(run --snapshot "${tmp}/tail.json")"
check "/data 非末位判拒绝" 1 "$?" "${out}" '"verdict": "not-last"'

cat >"${tmp}/hold.fstab" <<'EOF'
UUID=1111-1111  /      btrfs  defaults        0 0
UUID=2222-2222  /data  xfs    defaults        0 2
EOF
out="$(run --snapshot "${tmp}/dual.json" --fstab "${tmp}/hold.fstab")"
check "fstab 用 UUID 判可持久挂载" 0 "$?" "${out}" '"persist": "ok"'

cat >"${tmp}/name.fstab" <<'EOF'
UUID=1111-1111  /      btrfs  defaults        0 0
/dev/sdc1       /data  xfs    defaults        0 2
EOF
out="$(run --snapshot "${tmp}/dual.json" --fstab "${tmp}/name.fstab")"
check "fstab 按设备名写源判拒绝" 1 "$?" "${out}" '"persist": "unstable"'

printf '{"schema":99}\n' >"${tmp}/old.json"
out="$(run --snapshot "${tmp}/old.json")"
check "schema 不符按参数错误" 5 "$?" "${out}" "快照不可用"

out="$(run --snapshot "${tmp}/absent.json")"
check "快照不存在按参数错误" 5 "$?" "${out}" "快照不可用"

out="$(run --bogus)"
check "未知参数按参数错误" 5 "$?" "${out}" "未知参数"

out="$(run --help)"
check "帮助以 0 退出" 0 "$?" "${out}" "用法"

exit "${fails}"
