#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/channel-fetch.sh — 取回并复验发行通道清单（V-14-14）
#
# 用法: ci/gates/channel-fetch.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
#                                 [--workflow image-build.yml]
#                                 [--artifact airymaxos-acceptance]
#                                 [--name channel.json] [--limit 50]
#
# 发布时要挂到 Release 上的 channel.json，只能来自那台真正装配出 ISO 的
# 机器的产物，不能在本机重新生成一份：重新生成等于用当前源码再推一次清单，
# 而清单声明的是「线上那份字节」。0.0.3 的教训是发布页的描述与线上制品
# 各说各话——两边都能自证，却没有一处把二者对起来。本门禁负责从验收制品
# 里取回清单，再用客户端解析器（system/services/airyupdate/feed.py）复验
# 一遍：发布侧放行、客户端拒收的清单，等于把一次坏更新发给全体用户。
#
# 版本判定不靠 run 的元数据：workflow_dispatch 的输入（release_tag）不进
# run 列表，按分支或标题都取不到。判据改为制品内容——清单里的 version 与
# --tag 相等即命中。同一版本两次装配出不同内容属于发布事故，因此命中第一
# 份即停：更新的 run 就是更权威的证据，回落到旧 run 只会掩盖差异。
#
# 退出码: 0 已取回；1 无可用 run 或清单不可信；5 参数/环境不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ETRUST=3
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
FEED="${ROOT}/system/services"

TAG=""
REPO=""
DIR=""
WF="image-build.yml"
ART="airymaxos-acceptance"
NAME="channel.json"
LIMIT=50

usage()
{
    cat <<'EOF'
用法: ci/gates/channel-fetch.sh --tag vX.Y.Z --repo OWNER/REPO --dir DIR
                                [--workflow image-build.yml]
                                [--artifact airymaxos-acceptance]
                                [--name channel.json] [--limit 50]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)      TAG="${2:-}"; shift 2 ;;
        --repo)     REPO="${2:-}"; shift 2 ;;
        --dir)      DIR="${2:-}"; shift 2 ;;
        --workflow) WF="${2:-}"; shift 2 ;;
        --artifact) ART="${2:-}"; shift 2 ;;
        --name)     NAME="${2:-}"; shift 2 ;;
        --limit)    LIMIT="${2:-}"; shift 2 ;;
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

need_cmd gh python3 sha256sum
[ -d "${FEED}/airyupdate" ] ||
    { err "缺少客户端校验包: ${FEED}/airyupdate"; exit "${EINVAL}"; }
mkdir -p "${DIR}" || { err "无法创建目录: ${DIR}"; exit "${EINVAL}"; }

WANT="${TAG#v}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

PICK=""
MSG=""

# verify FILE —— 用客户端解析器复验，成功时把清单版本写入 PICK。
#
# 复用客户端的 feed.load_manifest 而不是在此重写字段校验：服务端与客户端
# 对「什么算可信清单」只能有一套口径，两边各写一份必然在某次改动后分叉。
verify()
{
    local out rc

    PICK=""
    MSG=""
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

# place SRC DEST —— 先写临时名再改名，避免留下半份清单被下游当成完整件。
place()
{
    cp "$1" "$2.part" && mv "$2.part" "$2"
}

group "gh run list ${WF}"
gh run list --workflow "${WF}" --repo "${REPO}" --status success \
    --limit "${LIMIT}" --json databaseId,createdAt >"${TMP}/runs.json" ||
    die "列举 ${WF} 运行记录失败（${REPO}）"
endgroup

ids="$(python3 -c '
import json, sys
for run in sorted(json.load(open(sys.argv[1])),
                  key=lambda r: r["createdAt"], reverse=True):
    print(run["databaseId"])
' "${TMP}/runs.json")" || die "运行记录不是合法 JSON"

[ -n "${ids}" ] || die "${REPO} 没有成功的 ${WF} 运行"

DEST="${DIR}/${NAME}"
while IFS= read -r id; do
    [ -n "${id}" ] || continue
    dl="${TMP}/run-${id}"
    if ! gh run download "${id}" --repo "${REPO}" --name "${ART}" \
            --dir "${dl}" >/dev/null 2>&1; then
        note "run ${id} 无 ${ART} 制品，跳过"
        continue
    fi
    src="${dl}/${NAME}"
    [ -f "${src}" ] || { note "run ${id} 制品内无 ${NAME}，跳过"; continue; }

    verify "${src}"
    rc=$?
    case "${rc}" in
    0) ;;
    "${ETRUST}") err "run ${id} 的 ${NAME} 不可信: ${MSG}"; exit 1 ;;
    *) err "客户端校验器不可用（rc=${rc}）: ${MSG}"; exit "${EINVAL}" ;;
    esac
    if [ "${PICK}" != "${WANT}" ]; then
        note "run ${id} 的清单版本 ${PICK}，非 ${WANT}，跳过"
        continue
    fi

    place "${src}" "${DEST}" || { err "写入失败: ${DEST}"; exit "${EINVAL}"; }
    say "通道清单: ${DEST}（run ${id}，sha256 $(sha256sum "${DEST}" |
        cut -d' ' -f1)）"
    exit 0
done <<<"${ids}"

die "没有 run 提供 ${WANT} 的 ${NAME}（${REPO} 近 ${LIMIT} 次成功的 ${WF}）"
