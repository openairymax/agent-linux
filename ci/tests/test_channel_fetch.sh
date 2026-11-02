#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_channel_fetch.sh — ci/gates/channel-fetch.sh 的分支回归
#
# 门禁取的是「另一台机器上的制品」，本地没有真实运行记录，所以按协议造
# 一个假 gh：run list 交出运行记录，run download 把制品目录复制出来。判定
# 逻辑因此可以在没有网络、没有 token 的机器上逐条打穿。
#
# 清单复验走真实解析器（system/services/airyupdate），不在这里另造一份：
# 本文件守的是「取回的是不是客户端认的那一份」，用假校验器就把被测对象
# 换掉了。
#
# 用法: bash ci/tests/test_channel_fetch.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/channel-fetch.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
FAKE="${tmp}/fake"
OUT="${tmp}/out"

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/bin" \
    "${FX}/system/services"
cp "${GATE}" "${FX}/ci/gates/channel-fetch.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp -a "${ROOT}/system/services/airyupdate" "${FX}/system/services/"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
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

run()
{
    PATH="${FX}/bin:${PATH}" FAKE="${FAKE}" \
        bash "${FX}/ci/gates/channel-fetch.sh" "$@" 2>&1
}

# 假 gh：只实现本门禁用到的两条子命令，其余一律退 2，避免「调用面扩大
# 而测试仍绿」。
cat >"${FX}/bin/gh" <<'EOF'
#!/usr/bin/env bash
case "${1:-} ${2:-}" in
"run list")
    if [ -e "${FAKE}/list-fail" ]; then
        exit 1
    fi
    cat "${FAKE}/runs.json"
    ;;
"run download")
    shift 2
    id="${1:-}"
    shift
    name="" dir=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
        --name) name="${2:-}"; shift 2 ;;
        --dir)  dir="${2:-}"; shift 2 ;;
        *)      shift ;;
        esac
    done
    [ -d "${FAKE}/art/${id}/${name}" ] || exit 1
    mkdir -p "${dir}"
    cp -a "${FAKE}/art/${id}/${name}/." "${dir}/"
    ;;
*) exit 2 ;;
esac
EOF
chmod +x "${FX}/bin/gh"

# mkfix — 每例从空制品根与空输出目录开始。
mkfix()
{
    rm -rf "${FAKE}" "${OUT}"
    mkdir -p "${FAKE}/art" "${OUT}"
}

# art <run-id> — 造一个验收制品目录。
art()
{
    mkdir -p "${FAKE}/art/$1/airymaxos-acceptance"
}

# chan <run-id> <版本> — 写一份符合客户端白名单的清单。
chan()
{
    cat >"${FAKE}/art/$1/airymaxos-acceptance/channel.json" <<EOF
{
  "schema_version": 1,
  "channel": "stable",
  "version": "$2",
  "build": 3,
  "released_at": "2026-11-02T00:00:00Z",
  "iso": {
    "name": "airymaxos-$2-x86_64.iso",
    "size": 1024,
    "sha256": "$(printf 'a%.0s' {1..64})"
  },
  "kernel": {
    "nevra": "airymaxos-kernel-$2-3.x86_64",
    "sha256": "$(printf 'b%.0s' {1..64})"
  },
  "notes_url": "https://github.com/openairymax/agent-linux/releases/tag/v$2"
}
EOF
}

# runs <id:createdAt> ... — 运行记录，参数顺序即 gh 的返回顺序。
runs()
{
    local pair first=1

    printf '[' >"${FAKE}/runs.json"
    for pair in "$@"; do
        [ "${first}" -eq 1 ] || printf ',' >>"${FAKE}/runs.json"
        first=0
        printf '{"databaseId":%s,"createdAt":"%s"}' \
            "${pair%%:*}" "${pair#*:}" >>"${FAKE}/runs.json"
    done
    printf ']\n' >>"${FAKE}/runs.json"
}

mkfix
art 11
chan 11 0.0.4
runs 11:2026-11-02T10:00:00Z
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "单一匹配 run 取回清单" 0 "$?" "${out}" "通道清单"
if cmp -s "${OUT}/channel.json" \
    "${FAKE}/art/11/airymaxos-acceptance/channel.json"; then
    ok "取回内容与制品逐字节一致"
else
    bad "取回内容与制品不一致"
fi

# 最新 run 装配的是别的版本：按期取回会拿到上一版清单，等于把旧版本当
# 新版本发出去。判据是清单内容，不是 run 的序号。
mkfix
art 22
chan 22 0.0.5
art 21
chan 21 0.0.4
runs 22:2026-11-02T12:00:00Z 21:2026-11-02T10:00:00Z
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "版本不符回落到较旧 run" 0 "$?" "${out}" "通道清单"
grep -q '"version": "0.0.4"' "${OUT}/channel.json" &&
    ok "回落取回的是本版清单" || bad "回落取回的版本不对"

# 最新的成功 run 可能只是验收轮（没有验收制品），不能因此判失败。
mkfix
art 31
chan 31 0.0.4
runs 32:2026-11-02T12:00:00Z 31:2026-11-02T10:00:00Z
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "无制品的最新 run 跳过" 0 "$?" "${out}" "通道清单"

mkfix
runs
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "无成功 run 退 1" 1 "$?" "${out}" "没有成功"

mkfix
mkdir -p "${FAKE}/art/41/airymaxos-acceptance"
runs 41:2026-11-02T10:00:00Z
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "制品内无清单退 1" 1 "$?" "${out}" "没有 run"

# notes_url 越域是客户端必然拒收的形态：发布侧不能把它挂上 Release。
mkfix
art 51
chan 51 0.0.4
sed -i 's#https://github.com/#https://example.com/#' \
    "${FAKE}/art/51/airymaxos-acceptance/channel.json"
runs 51:2026-11-02T10:00:00Z
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "清单越域判不可信" 1 "$?" "${out}" "不可信"
[ -e "${OUT}/channel.json" ] && bad "不可信清单仍落盘" ||
    ok "不可信清单不落盘"

mkfix
art 52
chan 52 0.0.4
sed -i '/schema_version/d' \
    "${FAKE}/art/52/airymaxos-acceptance/channel.json"
runs 52:2026-11-02T10:00:00Z
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "缺字段判不可信" 1 "$?" "${out}" "不可信"

mkfix
runs 61:2026-11-02T10:00:00Z
: >"${FAKE}/list-fail"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "列举运行记录失败退 1" 1 "$?" "${out}" "列举"

mkfix
printf 'not json\n' >"${FAKE}/runs.json"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "运行记录非 JSON 退 1" 1 "$?" "${out}" "不是合法 JSON"

out="$(run --tag v0.0.4 --repo o/r)"
check "缺 --dir 退 5" 5 "$?" "${out}" "用法"

out="$(run --repo o/r --dir "${OUT}")"
check "缺 --tag 退 5" 5 "$?" "${out}" "用法"

out="$(run --tag 0.0.4 --repo o/r --dir "${OUT}")"
check "tag 无 v 前缀退 5" 5 "$?" "${out}" "--tag"

out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}" --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

mv "${FX}/system/services" "${tmp}/svc.bak"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "缺客户端校验包退 5" 5 "$?" "${out}" "缺少客户端校验包"
mv "${tmp}/svc.bak" "${FX}/system/services"

if [ "${fails}" -ne 0 ]; then
    printf 'test_channel_fetch: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_channel_fetch: 全部通过\n'
