#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/channel-fetch.sh — 取回并复验发行通道清单（V-14-14）
#
# 用法: ci/gates/channel-fetch.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
#                                 [--workflow image-build.yml]
#                                 [--artifact airymaxos-acceptance]
#                                 [--name channel.json] [--arch ARCH]
#                                 [--optional] [--limit 50]
#
# 清单名按架构分（channel_name）：装有该架构 ISO 的验收制品里才有对应清单，
# 所以「没有任何 run 提供这份清单」等于该架构本版没有发行位。--optional 把
# 这件事实记成可接受并退出 0，用于可能没有该架构发行位的取件；缺省仍要求
# 清单存在，免得某轮漏挂被当成「这台架构本版没发」。缺失与不可信分开：
# 清单取到了却过不了客户端校验，有无 --optional 都失败。
#
# 发布时要挂到 Release 上的 channel.json，只能来自那台真正装配出 ISO 的
# 机器的产物，不能在本机重新生成一份：重新生成等于用当前源码再推一次清单，
# 而清单声明的是「线上那份字节」。0.0.3 的教训是发布页的描述与线上制品
# 各说各话——两边都能自证，却没有一处把二者对起来。本门禁负责从验收制品
# 里取回清单，再用客户端解析器（system/services/airyupdate/feed.py）复验
# 一遍：发布侧放行、客户端拒收的清单，等于把一次坏更新发给全体用户。
#
# 版本判定不靠 run 的分支或标题：workflow_dispatch 的输入（release_tag）不
# 进 run 列表，按名字取不到。判据是制品内容——清单里的 version 与 --tag 相
# 等即命中。同一版本两次装配出不同内容属于发布事故，因此命中第一份即停：
# 更新的 run 就是更权威的证据，回落到旧 run 只会掩盖差异。
#
# 另有一条来源判据：run 的提交必须等于 tag 指向的提交。tag 移动后，同版本
# 号的旧装配轮仍会列出，只按 version 匹配就会把上一提交的清单挂到新发行
# 页上——清单里的内核摘要对应的是旧构建，发布自证随即判分叉（0.0.5 重切
# 前的真实故障）。注解标签先解引用一层，比的是提交而不是标签对象。
#
# 制品名不是固定值：装配按矩阵项展开，验收制品带架构与腿名后缀
# （airymaxos-acceptance-x86_64、airymaxos-acceptance-aarch64-usb），同名
# 上传会在合并阶段 409。--artifact 因此是名字基：同名制品优先，其后按基名
# 加连字符的前缀逐个试，清单在哪一份里就从哪一份取。按名字取件而不按内容
# 猜，是为了让「取错制品」在日志里可见——每个尝试过的名字都会留下记录。
#
# 退出码: 0 已取回；1 无可用 run 或清单不可信；5 参数/环境不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ETRUST=3
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/channel.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/channel.sh"

WF="image-build.yml"
ART="airymaxos-acceptance"
LIMIT=50
OPTIONAL=0

usage()
{
    cat <<'EOF'
用法: ci/gates/channel-fetch.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
                                [--workflow image-build.yml]
                                [--artifact airymaxos-acceptance]
                                [--name channel.json] [--arch ARCH]
                                [--optional] [--limit 50]
EOF
}

while [ "$#" -gt 0 ]; do
    channel_opt "$1" "${2:-}" && { shift "${CH_SHIFT}"; continue; }
    case "$1" in
        --workflow) WF="${2:-}"; shift 2 ;;
        --artifact) ART="${2:-}"; shift 2 ;;
        --optional) OPTIONAL=1; shift ;;
        --limit)    LIMIT="${2:-}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

channel_need
need_cmd gh
mkdir -p "${CH_DIR}" || { err "无法创建目录: ${CH_DIR}"; exit "${EINVAL}"; }
channel_tmp

# place SRC DEST —— 先写临时名再改名，避免留下半份清单被下游当成完整件。
place()
{
    cp "$1" "$2.part" && mv "$2.part" "$2"
}

# tag_sha —— tag 指向的提交。注解标签的引用指向标签对象而不是提交，必须
# 再解一层，否则「同一提交」判据比较的对象根本不对。
tag_sha()
{
    local sha type

    gh api "repos/${CH_REPO}/git/ref/tags/${CH_TAG}" >"${TMP}/ref.json" \
        2>/dev/null ||
        { err "读不到标签: ${CH_TAG}（${CH_REPO}）"; return 1; }
    sha="$(python3 -c '
import json, sys
obj = json.load(open(sys.argv[1]))["object"]
print(obj["sha"], obj["type"])
' "${TMP}/ref.json")" ||
        { err "标签引用不是合法 JSON: ${CH_TAG}"; return 1; }
    type="${sha#* }"
    sha="${sha% *}"
    [ "${type}" = "tag" ] || { printf '%s\n' "${sha}"; return 0; }
    gh api "repos/${CH_REPO}/git/tags/${sha}" >"${TMP}/tag.json" \
        2>/dev/null ||
        { err "解不开注解标签: ${CH_TAG}"; return 1; }
    python3 -c '
import json, sys
print(json.load(open(sys.argv[1]))["object"]["sha"])
' "${TMP}/tag.json" ||
        { err "注解标签不是合法 JSON: ${CH_TAG}"; return 1; }
}

# absent MSG —— 「没有这份清单」的两档处理：--optional 记为可接受的事实并
# 退出 0，否则判失败。不可信不走这里，它在 take 里已经直接失败。
absent()
{
    if [ "${OPTIONAL}" -eq 1 ]; then
        note "$1（--optional，本轮不挂该清单）"
        exit 0
    fi
    die "$1"
}

TAG_SHA="$(tag_sha)" || exit 1

group "gh run list ${WF}"
gh run list --workflow "${WF}" --repo "${CH_REPO}" --status success \
    --limit "${LIMIT}" --json databaseId,createdAt,headSha \
    >"${TMP}/runs.json" ||
    die "列举 ${WF} 运行记录失败（${CH_REPO}）"
endgroup

ids="$(python3 -c '
import json, sys
for run in sorted(json.load(open(sys.argv[1])),
                  key=lambda r: r["createdAt"], reverse=True):
    print(run["databaseId"])
' "${TMP}/runs.json")" || die "运行记录不是合法 JSON"

[ -n "${ids}" ] || die "${CH_REPO} 没有成功的 ${WF} 运行"

# 只保留在 tag 提交上跑出的装配轮：同版本号的旧提交轮次不是本发行位的
# 凭证，它的清单对应另一份内核包。
ids="$(python3 -c '
import json, sys
want = sys.argv[1]
for run in sorted(json.load(open(sys.argv[2])),
                  key=lambda r: r["createdAt"], reverse=True):
    if run["headSha"] == want:
        print(run["databaseId"])
' "${TAG_SHA}" "${TMP}/runs.json")" || die "运行记录不是合法 JSON"

MISS="无 run 在 ${TAG_SHA:0:12} 装配 ${CH_WANT} 的 ${CH_NAME}"
MISS="${MISS}（${CH_REPO}，tag ${CH_TAG}）"
[ -n "${ids}" ] || absent "${MISS}"

DEST="${CH_DIR}/${CH_NAME}"

# pick — 制品名候选：同名在前，其后是基名加连字符的展开项。制品名不含空格，
# 故按空白切分即可；排序让「同名」优先，其余按字典序，取件顺序可复现。
pick()
{
    awk -v base="${ART}" '
        $0 == base { print 0, $0; next }
        index($0, base "-") == 1 { print 1, $0 }
    ' | sort -k1,1n -k2,2 | cut -d' ' -f2-
}

art_names()
{
    local id="$1" names
    names="$(gh api "repos/${CH_REPO}/actions/runs/${id}/artifacts?per_page=100" \
        --jq '.artifacts[].name' 2>/dev/null)" ||
        { note "run ${id} 制品清单不可读，跳过"; return 1; }
    printf '%s\n' "${names}"
}

fetch_one()
{
    local id="$1" name="$2" dl rc
    dl="${TMP}/run-${id}-${name}"
    gh run download "${id}" --repo "${CH_REPO}" --name "${name}" \
        --dir "${dl}" >/dev/null 2>&1 || return 1
    [ -f "${dl}/${CH_NAME}" ] || return 1
    channel_verify "${dl}/${CH_NAME}"
    rc=$?
    if [ "${rc}" -eq "${ETRUST}" ]; then
        err "run ${id} 的 ${CH_NAME} 不可信: ${CH_MSG}"
        return "${ETRUST}"
    fi
    [ "${rc}" -eq 0 ] || {
        err "客户端校验器不可用（rc=${rc}）: ${CH_MSG}"
        return "${EINVAL}"
    }
    [ "${CH_PICK}" = "${CH_WANT}" ] || return 1
    place "${dl}/${CH_NAME}" "${DEST}" ||
        { err "写入失败: ${DEST}"; return "${EINVAL}"; }
    say "通道清单: ${DEST}（run ${id}，制品 ${name}，sha256 $(sha256sum "${DEST}" |
        cut -d' ' -f1)）"
    return 0
}

# take <run-id> —— 0 已取回；1 本 run 无本版清单；3 清单不可信；5 环境不满足。
take()
{
    local id="$1" name rc names
    names="$(art_names "${id}")" || return 1
    while IFS= read -r name; do
        [ -n "${name}" ] || continue
        fetch_one "${id}" "${name}"
        rc=$?
        case "${rc}" in
        0) return 0 ;;
        1) ;;
        *) return "${rc}" ;;
        esac
    done < <(printf '%s\n' "${names}" | pick)
    note "run ${id} 无 ${ART} 制品或制品内无 ${CH_WANT} 的 ${CH_NAME}，跳过"
    return 1
}

while IFS= read -r id; do
    [ -n "${id}" ] || continue
    take "${id}"
    rc=$?
    case "${rc}" in
    0) exit 0 ;;
    "${ETRUST}") exit 1 ;;
    "${EINVAL}") exit "${EINVAL}" ;;
    esac
done <<<"${ids}"

absent "没有 run 提供 ${CH_WANT} 的 ${CH_NAME}（${CH_REPO} 近 ${LIMIT} 次成功的 ${WF}）"
