#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_disk_layout.sh — ci/gates/disk-layout.sh 的分支回归
#
# runner 上没有受测机器，门禁的常规入口是重放快照。这里用五份手工构造的
# status 快照驱动全部分支：双盘与单盘两种合规形态、缺 /data、/data 非
# 末位分区、快照本身不可读。断言只看退出码与稳定 token，不看措辞。
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

root="$(part nvme0n1p2 btrfs '"/"' system)"
data="$(part sdc1 xfs '"/data"' data)"
swap="$(part sdc2 swap null swap)"

snap "${tmp}/dual.json" multi \
    "$(disk nvme0n1 nvme "${root}"),$(disk sdc sata "${data}")"
out="$(run --snapshot "${tmp}/dual.json")"
check "双盘：/ 与 /data 各自独立" 0 "$?" "${out}" '"verdict": "ok"'

snap "${tmp}/single.json" single "$(disk sda sata "${root},${data}")"
out="$(run --snapshot "${tmp}/single.json")"
check "单盘：同盘切分且 /data 末位" 0 "$?" "${out}" '"verdict": "ok"'
case "${out}" in
*'"kind": "single"'*) ok "单盘 token 正确" ;;
*) bad "单盘 token 不正确"; printf '%s\n' "${out}" >&2 ;;
esac

snap "${tmp}/bare.json" single "$(disk sda sata "${root}")"
out="$(run --snapshot "${tmp}/bare.json")"
check "缺 /data 判拒绝" 1 "$?" "${out}" '"verdict": "no-data"'

snap "${tmp}/tail.json" single "$(disk sda sata "${root},${data},${swap}")"
out="$(run --snapshot "${tmp}/tail.json")"
check "/data 非末位判拒绝" 1 "$?" "${out}" '"verdict": "not-last"'

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
