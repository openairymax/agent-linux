#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/channel.sh — 发行通道清单的取回与发布共用件
#
# 被 channel-fetch.sh（从验收制品取回清单）与 channel-publish.sh（把清单挂回
# 发布页）共用。两处的「什么算可信清单」必须只有一套口径：客户端解析器只
# 能在一处被调用，服务端另写一份校验，必然在某次字段变动后与客户端分叉，
# 而分叉的后果是把一次坏更新发给全体用户。
#
# 同样收口的还有三件同形事项：
#
#   * 共同选项（--tag/--repo/--dir/--name）的解析——两边各写一遍参数循环，
#     选项名与取值规则就会在某一侧漂移；
#   * 前置条件（tag 形态、客户端校验包在位、所需命令）；
#   * 临时目录与退出清理：两处各写一次 trap，漏掉的一侧会留下半份清单。
#
# 依赖调用方先 source ci/lib/log.sh 并定义 usage()；EINVAL 由调用方持有，
# 未定义时按 5 处理。本库不设 set -euo：调用方的 shell 选项由调用方决定。
# ===========================================================================

# 公共选项的取值面。通道门禁的全部选项都是「选项 + 值」两段式，因此共用
# 解析只需回报消费了几个参数，其余选项交回调用方的 case 分支。
# shellcheck disable=SC2034  # 这些变量由 channel-fetch / channel-publish 在 source 时读取
CH_TAG=""
CH_REPO=""
CH_DIR=""
CH_NAME="channel.json"
CH_SHIFT=0
CH_PICK=""
CH_MSG=""
CH_FEED="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)/system/services"

# channel_opt ARG VALUE —— 命中共同选项时写入取值并置 CH_SHIFT=2，返回 0；
# 否则返回 1，由调用方处理自己的私有选项。取值由调用方传入：函数内的 $2
# 是函数自己的位置参数，若在函数里取「下一个参数」会取到空串。
channel_opt()
{
    case "$1" in
        --tag)  CH_TAG="$2" ;;
        --repo) CH_REPO="$2" ;;
        --dir)  CH_DIR="$2" ;;
        --name) CH_NAME="$2" ;;
        *) return 1 ;;
    esac
    CH_SHIFT=2
    return 0
}

# channel_need —— 前置条件。失败一律退 EINVAL：这是调用方式或环境问题，
# 不是清单本身不可信，两者混在同一个退出码里会让流水线误判。
channel_need()
{
    [ -n "${CH_TAG}" ] && [ -n "${CH_REPO}" ] && [ -n "${CH_DIR}" ] || {
        usage >&2; exit "${EINVAL:-5}"; }
    case "${CH_TAG}" in
    v[0-9]*) ;;
    *) err "--tag 需形如 vX.Y.Z: ${CH_TAG}"; exit "${EINVAL:-5}" ;;
    esac
    need_cmd python3 sha256sum
    [ -d "${CH_FEED}/airyupdate" ] ||
        { err "缺少客户端校验包: ${CH_FEED}/airyupdate"; exit "${EINVAL:-5}"; }
    CH_WANT="${CH_TAG#v}"
}

# channel_tmp —— 临时目录与退出清理。
channel_tmp()
{
    TMP="$(mktemp -d)"
    trap 'rm -rf "${TMP}"' EXIT
}

# channel_verify FILE —— 用客户端解析器复验：成功把清单版本写入 CH_PICK，
# 失败把解析器原话写入 CH_MSG。复用 feed.load_manifest 而不是在此重写字段
# 校验：服务端与客户端对「什么算可信清单」只能有一套口径。
channel_verify()
{
    local out rc

    CH_PICK=""
    CH_MSG=""
    out="$(PYTHONPATH="${CH_FEED}" python3 -c '
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
        0) CH_PICK="${out}" ;;
        *) CH_MSG="${out}" ;;
    esac
    return "${rc}"
}
