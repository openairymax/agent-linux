#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_channel_publish.sh — ci/gates/channel-publish.sh 的分支回归
#
# 门禁要动的是线上发布页，本地没有可写的 Release，所以按 GitHub 资产接口的
# 形状造一个假 curl：读 Release、删资产、传资产、取字节各一条，状态落在临时
# 目录里，于是「先删后传」「上传后读回」「旧件清空」都能逐条打穿。
#
# 清单复验走真实解析器（system/services/airyupdate），不在这里另造校验：本
# 文件守的是「挂上去的是不是客户端认的那一份」，用假校验器就把被测对象换掉。
#
# 用法: bash ci/tests/test_channel_publish.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/channel-publish.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
FAKE="${tmp}/fake"
OUT="${tmp}/out"

mkdir -p "${FX}/bin" "${FAKE}" "${OUT}"

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

# run ARGS... —— 以假 curl 在 PATH 首位运行真实门禁；令牌走 GITHUB_TOKEN。
run()
{
    PATH="${FX}/bin:${PATH}" FAKE="${FAKE}" GITHUB_TOKEN=faketoken \
        bash "${GATE}" "$@" 2>&1
}

# 假 curl：只实现资产接口的四条路径，其余退 2，避免「调用面扩大而测试仍绿」。
cat >"${FX}/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -uo pipefail
method=GET out=/dev/null url="" body=""
while [ "$#" -gt 0 ]; do
case "$1" in
-X)             method="${2:-}"; shift 2 ;;
-o)             out="${2:-}"; shift 2 ;;
-w|-H)          shift 2 ;;
--data-binary)  body="${2#@}"; shift 2 ;;
-*)             shift ;;
*)              url="$1"; shift ;;
esac
done

S="${FAKE}/state"
code=200

rel_json()
{
    local first=1 id name
    printf '{"id":7,"assets":['
    while read -r id name; do
        [ -n "${id}" ] || continue
        [ "${first}" -eq 1 ] || printf ','
        first=0
        printf '{"id":%s,"name":"%s"}' "${id}" "${name}"
    done <"${S}/rel"
    printf ']}'
}

if [ "${method}" = POST ]; then
    if [ -e "${FAKE}/fail-upload" ]; then
        code=500
        : >"${out}"
    else
        nid=$(( $(cat "${S}/next") + 1 ))
        printf '%s\n' "${nid}" >"${S}/next"
        cp "${body}" "${S}/bytes/${nid}"
        printf '%s %s\n' "${nid}" "${url##*name=}" >>"${S}/rel"
        printf '{"id":%s,"name":"%s"}' "${nid}" "${url##*name=}" >"${out}"
        code=201
    fi
elif [ "${method}" = DELETE ]; then
    sed -i "/^${url##*/} /d" "${S}/rel"
    rm -f "${S}/bytes/${url##*/}"
    : >"${out}"
    code=204
else
    case "${url}" in
    */releases/assets/*)
        id="${url##*/}"
        if [ -f "${S}/bytes/${id}" ]; then
            if [ -e "${FAKE}/corrupt" ]; then
                printf 'tampered\n' >"${out}"
            else
                cp "${S}/bytes/${id}" "${out}"
            fi
        else
            code=404
            : >"${out}"
        fi
        ;;
    */releases/tags/*)
        if [ -e "${FAKE}/no-release" ]; then
            code=404
            : >"${out}"
        else
            rel_json >"${out}"
        fi
        ;;
    *) exit 2 ;;
    esac
fi
printf '%s' "${code}"
EOF
chmod +x "${FX}/bin/curl"

# mkfix —— 每例从空页面与空清单目录开始：页面 id 7，资产 id 从 100 起。
mkfix()
{
    rm -rf "${FAKE}" "${OUT}"
    mkdir -p "${FAKE}/state/bytes" "${OUT}"
    : >"${FAKE}/state/rel"
    printf '100\n' >"${FAKE}/state/next"
}

# chan <版本> —— 写一份符合客户端白名单的清单。
chan()
{
    cat >"${OUT}/channel.json" <<EOF
{
  "schema_version": 1,
  "channel": "stable",
  "version": "$1",
  "build": 3,
  "released_at": "2026-11-02T00:00:00Z",
  "iso": {
    "name": "airymaxos-$1-x86_64.iso",
    "size": 1024,
    "sha256": "$(printf 'a%.0s' {1..64})"
  },
  "kernel": {
    "nevra": "airymaxos-kernel-$1-3.x86_64",
    "sha256": "$(printf 'b%.0s' {1..64})"
  },
  "notes_url": "https://github.com/openairymax/agent-linux/releases/tag/v$1"
}
EOF
}

# have <id> <name> —— 在假页面上放一件旧资产，字节内容记为旧清单。
have()
{
    printf '%s %s\n' "$1" "$2" >>"${FAKE}/state/rel"
    printf 'stale\n' >"${FAKE}/state/bytes/$1"
}

# count —— 假页面上同名清单的件数。
count()
{
    awk '$2 == "channel.json"' "${FAKE}/state/rel" | wc -l
}

mkfix
chan 0.0.4
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "页面无清单时新增" 0 "$?" "${out}" "已发布"
[ "$(count)" -eq 1 ] && ok "新增后恰好一件" || bad "新增后件数不为 1"
grep -q '"version": "0.0.4"' \
    "${FAKE}/state/bytes/$(awk '$2 == "channel.json" { print $1 }' \
        "${FAKE}/state/rel")" &&
    ok "页面字节即本地清单" || bad "页面字节不是本地清单"

# 清单名随 --arch 派生：aarch64 的清单以 channel-aarch64.json 挂上同一页面，
# 与 x86_64 的 channel.json 并存，互不顶替。
mkfix
chan 0.0.4
run --tag v0.0.4 --repo o/r --dir "${OUT}" >/dev/null 2>&1
cp "${OUT}/channel.json" "${OUT}/channel-aarch64.json"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}" --name channel-aarch64.json)"
check "aarch64 清单独立挂载" 0 "$?" "${out}" "已发布"
awk '$2 == "channel-aarch64.json"' "${FAKE}/state/rel" | grep -q . &&
    ok "页面出现 channel-aarch64.json" || bad "页面缺 channel-aarch64.json"
[ "$(count)" -eq 1 ] && ok "x86_64 清单仍在" || bad "x86_64 清单被顶替"

# 同 tag 重建 ISO 后回写：旧件必须先删，否则同名上传被 422 拒回。
mkfix
chan 0.0.4
have 3 channel.json
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "同名旧件按替换处理" 0 "$?" "${out}" "收回旧 channel.json"
[ "$(count)" -eq 1 ] && ok "替换后仍为一件" || bad "替换后件数不为 1"
if grep -q stale "${FAKE}/state/bytes/3" 2>/dev/null; then
    bad "旧字节仍在（未真正删除）"
else
    ok "旧资产连同字节一并收回"
fi
awk '$2 == "channel.json" && $1 != 3' "${FAKE}/state/rel" | grep -q . &&
    ok "页面指向新资产 id" || bad "页面仍指向旧 id"

# 历史轮次可能留下多件同名资产：一次清空，而不是只删第一件。
mkfix
chan 0.0.4
have 3 channel.json
have 4 channel.json
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "重复旧件一并收回" 0 "$?" "${out}" "已发布"
[ "$(count)" -eq 1 ] && ok "重复旧件清空" || bad "页面仍有多件同名"

# 版本不同源：拿上一轮的清单回写本版，会把装机端指向旧字节。
mkfix
chan 0.0.5
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "版本不同源判失败" 1 "$?" "${out}" "不同源"
[ "$(count)" -eq 0 ] && ok "不同源未上传" || bad "不同源仍上传"

# 客户端不认的清单不允许出现在发布页。
mkfix
chan 0.0.4
sed -i '/schema_version/d' "${OUT}/channel.json"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "清单不可信判失败" 1 "$?" "${out}" "不可信"
[ "$(count)" -eq 0 ] && ok "不可信清单未上传" || bad "不可信清单仍上传"

mkfix
chan 0.0.4
: >"${FAKE}/corrupt"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "读回字节不符判失败" 1 "$?" "${out}" "读回字节与本地不一致"

mkfix
chan 0.0.4
: >"${FAKE}/fail-upload"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "上传失败判失败" 1 "$?" "${out}" "上传 channel.json 失败"

# 首版引导：建页在 release.yml 的第 3 步，装配轮先于它运行。此时没有可回写
# 的对象，门禁必须放行；清单随 airymaxos-acceptance 交由建页轮取用。
mkfix
chan 0.0.4
: >"${FAKE}/no-release"
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}")"
check "发布页尚未建时跳过回写" 0 "$?" "${out}" "跳过回写"
[ "$(count)" -eq 0 ] && ok "无页面时未凭空上传" || bad "无页面仍上传"

mkfix
chan 0.0.4
have 3 channel.json
out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}" --dry-run)"
check "演练不改页面" 0 "$?" "${out}" "未上传"
[ "$(count)" -eq 1 ] && [ -f "${FAKE}/state/bytes/3" ] &&
    ok "演练保留旧件" || bad "演练动了页面"

mkfix
chan 0.0.4
out="$(PATH="${FX}/bin:${PATH}" FAKE="${FAKE}" env -u GITHUB_TOKEN \
    -u TOKEN -u GH_TOKEN bash "${GATE}" --tag v0.0.4 --repo o/r \
    --dir "${OUT}" 2>&1)"
check "缺令牌退 5" 5 "$?" "${out}" "缺少 GITHUB_TOKEN"

out="$(run --repo o/r --dir "${OUT}")"
check "缺 --tag 退 5" 5 "$?" "${out}" "用法"

out="$(run --tag 0.0.4 --repo o/r --dir "${OUT}")"
check "tag 无 v 前缀退 5" 5 "$?" "${out}" "--tag"

out="$(run --tag v0.0.4 --repo o/r --dir "${OUT}" --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

if [ "${fails}" -ne 0 ]; then
    printf 'test_channel_publish: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_channel_publish: 全部通过\n'
