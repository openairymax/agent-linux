#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/channel-publish.sh — 把装配轮产出的通道清单挂回该 tag 的发布页
#
# 用法: ci/gates/channel-publish.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
#                                  [--name channel.json] [--dry-run]
#
# 客户端发现新版本的唯一入口是发布页上的 channel.json（releases/latest/
# download/channel.json）。装配轮为同一个 tag 重建 ISO 之后，页面上的清单若
# 仍指着上一轮的字节，装机端拿到的就是「新版本号 + 旧摘要」——两侧各自都能
# 自证，却没有一处把它们对起来（0.0.4 收尾期的实际形态：页面仍是 build 19 /
# 内核 0.0.4-7，而线上 ISO 已是重建后的字节）。建页与首次上传都在 release.yml
# 的首轮里做，重建轮没有回写路径，本门禁补的就是这条：写前用客户端解析器复验
# 版本同源，写后按资产 id 取回比对字节，避免把「上传返回 201」当成发布完成。
#
# 替换只能「先删后传」：Release 资产名唯一，同名上传被 422 拒回，而旧清单留在
# 页面上比缺件更难察觉——它看起来是完整的。
#
# 退出码: 0 已发布且读回一致；1 复验/上传/读回不符；3 发布页不存在；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/release-assets.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/release-assets.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
FEED="${ROOT}/system/services"

TAG=""
REPO="${GITHUB_REPOSITORY:-}"
DIR=""
NAME="channel.json"
DRY=0

usage()
{
    cat <<'EOF'
用法: ci/gates/channel-publish.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
                                  [--name channel.json] [--dry-run]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)      TAG="${2:-}"; shift 2 ;;
        --repo)     REPO="${2:-}"; shift 2 ;;
        --dir)      DIR="${2:-}"; shift 2 ;;
        --name)     NAME="${2:-}"; shift 2 ;;
        --dry-run)  DRY=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TAG}" ] && [ -n "${REPO}" ] && [ -n "${DIR}" ] || {
    usage >&2; exit "${EINVAL}"; }
case "${TAG}" in
v[0-9]*) ;;
*) err "--tag 需形如 vX.Y.Z: ${TAG}"; exit "${EINVAL}" ;;
esac

need_cmd python3 sha256sum curl cmp
[ -d "${FEED}/airyupdate" ] ||
    { err "缺少客户端校验包: ${FEED}/airyupdate"; exit "${EINVAL}"; }
[ -f "${DIR}/${NAME}" ] || { err "缺少清单: ${DIR}/${NAME}"; exit "${EINVAL}"; }

TOKEN="${TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
[ -n "${TOKEN}" ] || { err "缺少 GITHUB_TOKEN"; exit "${EINVAL}"; }
export TOKEN

WANT="${TAG#v}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

PICK=""
MSG=""
RID=""
HAVE=""

# verify FILE —— 客户端解析器复验，成功时把清单版本写入 PICK。
#
# 与 channel-fetch 同一处口径：什么算可信清单只能由客户端代码判定，服务端
# 另写一份校验必然在某次字段变动后与客户端分叉。
verify()
{
    local out rc

    out="$(PYTHONPATH="${FEED}" python3 -c '
import sys
from airyupdate.feed import Untrusted, load_manifest
try:
    manifest = load_manifest(open(sys.argv[1], "rb").read())
except Untrusted as exc:
    sys.stderr.write(str(exc) + "\n")
    sys.exit(3)
print(manifest.version)
' "$1" 2>&1)"
    rc=$?
    case "${rc}" in
        0) PICK="${out}" ;;
        *) MSG="${out}" ;;
    esac
    return "${rc}"
}

# json_id FILE —— 取对象 id。上传与建页两处都只需要这一个字段。
json_id()
{
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$1"
}

# rel_of —— 取 Release 与其资产清单；RID 是 Release id，HAVE 是同名资产的
# 现有 id 列表（可能多于一件：历史轮次留下过重复）。结果走全局而不是标准
# 输出——命令替换会另起子 shell，赋值留在里面就丢了。
rel_of()
{
    airy_asset_have "${REPO}" "${TAG}" "${TMP}/have" "${TMP}/rel.json" ||
        return $?
    RID="$(json_id "${TMP}/rel.json")" || return 1
    HAVE="$(awk -v n="${NAME}" '$2 == n { print $1 }' "${TMP}/have")"
    return 0
}

# drop_old —— 收回页面上的同名旧件。列表为空时循环不执行，正好覆盖首轮。
drop_old()
{
    local id

    for id in ${HAVE}; do
        airy_asset_del "${REPO}" "${id}" || return 1
        say "收回旧 ${NAME}（id ${id}）"
    done
    return 0
}

# single —— 上传后页面上同名资产恰好一件。多于一件说明删除没生效。
single()
{
    local n

    airy_asset_have "${REPO}" "${TAG}" "${TMP}/have2" "${TMP}/rel2.json" ||
        return 1
    n="$(awk -v n="${NAME}" '$2 == n' "${TMP}/have2" | wc -l)"
    if [ "${n}" -ne 1 ]; then
        err "页面上 ${NAME} 应有 1 件，实得 ${n}"
        return 1
    fi
    return 0
}

# readback ID —— 按资产 id 取回字节与本地逐字节比对。摘要对账只能证明
# 「文件没变」，这里要证明「页面上挂的就是这一份」。
readback()
{
    local back="${TMP}/back"

    airy_asset_get "${REPO}" "$1" "${back}" || return 1
    if ! cmp -s "${DIR}/${NAME}" "${back}"; then
        err "读回字节与本地不一致: ${NAME}"
        return 1
    fi
    say "读回 id $1 字节一致"
    return 0
}

group "复验 ${NAME}"
verify "${DIR}/${NAME}" || { err "清单不可信：${MSG}"; exit 1; }
[ "${PICK}" = "${WANT}" ] ||
    { err "清单版本 ${PICK} 与 ${TAG} 不同源，拒绝发布"; exit 1; }
say "版本 ${PICK}，客户端解析器通过"
endgroup

group "发布页 ${REPO} ${TAG}"
rel_of
rc=$?
case "${rc}" in
0) ;;
"${AIRY_ASSETS_ENOREL}") err "发布页不存在: ${REPO} ${TAG}"; exit 3 ;;
*) exit 1 ;;
esac
say "Release id ${RID}，当前 $(wc -l <"${TMP}/have") 件资产"
if [ -n "${HAVE}" ]; then
    say "页面上已有同名 ${NAME}（旧 id ${HAVE//$'\n'/ }），按替换处理"
else
    say "页面上尚无 ${NAME}，按新增处理"
fi

if [ "${DRY}" -eq 1 ]; then
    say "演练：${NAME} 未上传（预案正确）"
    endgroup
    exit 0
fi

drop_old || exit 1
airy_asset_put "${REPO}" "${RID}" "${DIR}/${NAME}" "${NAME}" \
    "${TMP}/put.json" || exit 1
NID="$(json_id "${TMP}/put.json")" || { err "上传回执无法解析"; exit 1; }
single || exit 1
readback "${NID}" || exit 1
say "已发布 ${NAME}：$(sha256sum "${DIR}/${NAME}" | cut -c1-16)…"
endgroup

say "通道清单发布完成: ${REPO} ${TAG} ${NAME}"
