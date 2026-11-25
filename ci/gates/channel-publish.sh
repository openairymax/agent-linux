#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
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
# 首版引导时发布页还不存在：建页在 release.yml 的第 3 步，装配轮先于它运行。
# 此时没有可回写的对象，清单本身仍随 airymaxos-acceptance 上传，由建页轮的
# channel-fetch 取用。这里按无操作放行，否则「页面尚未建」会被判成「回写失败」，
# 引导链在第 2 步断掉，第 3 步取不到成功的装配轮。
#
# 退出码: 0 已发布且读回一致，或发布页尚未建（无操作）；1 复验/上传/读回不符；
#         5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/channel.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/channel.sh"
# shellcheck source=../lib/release_assets.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/release_assets.sh"

CH_REPO="${GITHUB_REPOSITORY:-}"
DRY=0

usage()
{
    cat <<'EOF'
用法: ci/gates/channel-publish.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
                                  [--name channel.json] [--dry-run]
EOF
}

while [ "$#" -gt 0 ]; do
    channel_opt "$1" "${2:-}" && { shift "${CH_SHIFT}"; continue; }
    case "$1" in
        --dry-run)  DRY=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

channel_need
need_cmd curl cmp
[ -f "${CH_DIR}/${CH_NAME}" ] ||
    { err "缺少清单: ${CH_DIR}/${CH_NAME}"; exit "${EINVAL}"; }

TOKEN="${TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
[ -n "${TOKEN}" ] || { err "缺少 GITHUB_TOKEN"; exit "${EINVAL}"; }
export TOKEN

channel_tmp

RID=""
HAVE=""

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
    airy_asset_have "${CH_REPO}" "${CH_TAG}" "${TMP}/have" "${TMP}/rel.json" ||
        return $?
    RID="$(json_id "${TMP}/rel.json")" || return 1
    HAVE="$(awk -v n="${CH_NAME}" '$2 == n { print $1 }' "${TMP}/have")"
    return 0
}

# drop_old —— 收回页面上的同名旧件。列表为空时循环不执行，正好覆盖首轮。
drop_old()
{
    local id

    for id in ${HAVE}; do
        airy_asset_del "${CH_REPO}" "${id}" || return 1
        say "收回旧 ${CH_NAME}（id ${id}）"
    done
    return 0
}

# single —— 上传后页面上同名资产恰好一件。多于一件说明删除没生效。
single()
{
    local n

    airy_asset_have "${CH_REPO}" "${CH_TAG}" "${TMP}/have2" "${TMP}/rel2.json" ||
        return 1
    n="$(awk -v n="${CH_NAME}" '$2 == n' "${TMP}/have2" | wc -l)"
    if [ "${n}" -ne 1 ]; then
        err "页面上 ${CH_NAME} 应有 1 件，实得 ${n}"
        return 1
    fi
    return 0
}

# readback ID —— 按资产 id 取回字节与本地逐字节比对。摘要对账只能证明
# 「文件没变」，这里要证明「页面上挂的就是这一份」。
readback()
{
    local back="${TMP}/back"

    airy_asset_get "${CH_REPO}" "$1" "${back}" || return 1
    if ! cmp -s "${CH_DIR}/${CH_NAME}" "${back}"; then
        err "读回字节与本地不一致: ${CH_NAME}"
        return 1
    fi
    say "读回 id $1 字节一致"
    return 0
}

group "复验 ${CH_NAME}"
channel_verify "${CH_DIR}/${CH_NAME}" || { err "清单不可信：${CH_MSG}"; exit 1; }
[ "${CH_PICK}" = "${CH_WANT}" ] ||
    { err "清单版本 ${CH_PICK} 与 ${CH_TAG} 不同源，拒绝发布"; exit 1; }
say "版本 ${CH_PICK}，客户端解析器通过"
endgroup

group "发布页 ${CH_REPO} ${CH_TAG}"
rel_of
rc=$?
if [ "${rc}" -eq "${AIRY_ASSETS_ENOREL}" ]; then
    say "发布页尚未建，跳过回写（清单随验收制品供建页轮取用）"
    endgroup
    exit 0
fi
[ "${rc}" -eq 0 ] || exit 1
say "Release id ${RID}，当前 $(wc -l <"${TMP}/have") 件资产"
if [ -n "${HAVE}" ]; then
    say "页面上已有同名 ${CH_NAME}（旧 id ${HAVE//$'\n'/ }），按替换处理"
else
    say "页面上尚无 ${CH_NAME}，按新增处理"
fi

if [ "${DRY}" -eq 1 ]; then
    say "演练：${CH_NAME} 未上传（预案正确）"
    endgroup
    exit 0
fi

drop_old || exit 1
airy_asset_put "${CH_REPO}" "${RID}" "${CH_DIR}/${CH_NAME}" "${CH_NAME}" \
    "${TMP}/put.json" || exit 1
NID="$(json_id "${TMP}/put.json")" || { err "上传回执无法解析"; exit 1; }
single || exit 1
readback "${NID}" || exit 1
say "已发布 ${CH_NAME}：$(sha256sum "${CH_DIR}/${CH_NAME}" | cut -c1-16)…"
endgroup

say "通道清单发布完成: ${CH_REPO} ${CH_TAG} ${CH_NAME}"
