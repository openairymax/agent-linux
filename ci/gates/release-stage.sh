#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-stage.sh — 稳定版何时成为 latest 由验收证据决定（REL-02）
#
# 用法: ci/gates/release-stage.sh --tag TAG [--repo OWNER/NAME]
#                                 [--stage hold|promote|check]
#                                 [--expect held|latest|prerelease]
#                                 [--evidence FILE]
#
# releases/latest 不是展示位，是更新通道的入口：客户端的 channel.json 就从
# 它下面取。页面一建就置 make_latest，等于把还没验收的构建推给所有装机。
# 0.0.3 收尾期就是这么出的——G-UKUI-1 尚未取证，stable 页已经在 latest 上。
#
# 根因是发布流水线手里永远没有验收证据：ISO 的 B 轮安装在 image-build，不在
# release。所以「谁有权置 latest」只能是发布机上的显式动作，本门禁把阶段
# 收敛成三个可判状态：
#   prerelease  预发布页，永不进 latest（-rc / -beta）
#   held        正式页，已创建但不是 latest（稳定版刚发布时的唯一合法态）
#   latest      已由 promote 打开
#
# hold     幂等：只把 make_latest 收回 false，不动 prerelease 位；
# promote  需要 --evidence，文件内必须出现 AIRYMAX-SMOKE-PASS；
# check    只读；给 --expect 时按上面三个状态断言。
#
# 退出码: 0 断言成立；1 证据不足或读回不符；3 Release 不存在；5 参数/令牌。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ENOREL=3
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/release-assets.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/release-assets.sh"

REPO="${GITHUB_REPOSITORY:-}"
TAG=""
STAGE="check"
EXPECT=""
EVIDENCE=""
WORK=""
LAST=""
REL_ID=""
REL_PRE=""
REL_DRAFT=""
REL_TAG=""

usage()
{
    cat <<'EOF'
用法: ci/gates/release-stage.sh --tag TAG [选项]

  --tag TAG        要处置的发布标签，如 v0.0.4
  --repo OWNER/NAME  目标仓库，缺省 GITHUB_REPOSITORY
  --stage hold|promote|check  动作，缺省 check
  --expect held|latest|prerelease  check 的期望阶段，缺省只打印
  --evidence FILE  promote 所需的验收证据（TAP 或串口日志）
  -h, --help       显示本帮助

阶段由页面本身决定：prerelease 位为真即预发布；否则看是否为
releases/latest。hold 只收 make_latest，promote 才同时清 prerelease
并把页面推成 latest。
EOF
}

parse_args()
{
    while [ "$#" -gt 0 ]; do
        case "$1" in
        --tag)      TAG="${2:-}"; shift 2 ;;
        --repo)     REPO="${2:-}"; shift 2 ;;
        --stage)    STAGE="${2:-}"; shift 2 ;;
        --expect)   EXPECT="${2:-}"; shift 2 ;;
        --evidence) EVIDENCE="${2:-}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *)          err "未知参数: $1"; exit "${EINVAL}" ;;
        esac
    done
}

check_args()
{
    [ -n "${TAG}" ] || { err "缺少 --tag"; exit "${EINVAL}"; }
    [ -n "${REPO}" ] || { err "缺少 --repo（或 GITHUB_REPOSITORY）"; exit "${EINVAL}"; }
    case "${STAGE}" in
    hold|promote|check) ;;
    *) err "--stage 只能是 hold / promote / check: ${STAGE}"; exit "${EINVAL}" ;;
    esac
    case "${EXPECT}" in
    ''|held|latest|prerelease) ;;
    *) err "--expect 只能是 held / latest / prerelease: ${EXPECT}"; exit "${EINVAL}" ;;
    esac
    if [ "${STAGE}" = promote ] && [ -z "${EVIDENCE}" ]; then
        err "promote 必须给 --evidence（B 轮验收的 TAP 或串口日志）"
        exit "${EINVAL}"
    fi
    # 写操作缺令牌只会拿到 401，提前说清楚，免得被读成「页面不存在」。
    if [ "${STAGE}" != check ] && [ -z "${TOKEN:-}" ]; then
        err "写操作需要 TOKEN / GITHUB_TOKEN"
        exit "${EINVAL}"
    fi
}

# 页面对象的四个字段一次取出，后续断言都基于同一份 JSON。
rel_fields()
{
    python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    rel = json.load(fh)
print(rel.get("id", ""), str(rel.get("prerelease")).lower(),
      str(rel.get("draft")).lower(), rel.get("tag_name", ""))
PY
}

# 404 是「还没建」，不是「读不到」：两者的处置完全不同（前者退 3 让人去
# 完成发布，后者说明令牌或网络坏了）。
rel_get()
{
    local code
    code="$(airy_assets_api GET \
        "${AIRY_ASSETS_API}/repos/${REPO}/releases/tags/${TAG}" "$1")"
    case "${code}" in
    200) return 0 ;;
    404) return "${ENOREL}" ;;
    *) err "读取 Release ${TAG} 失败: HTTP ${code}"; return 1 ;;
    esac
}

# 当前 latest 的 tag，写进 LAST。仓库还没有正式版时，接口返回 404，按
# 「无」处理而不是失败。
last_tag()
{
    local code
    code="$(airy_assets_api GET \
        "${AIRY_ASSETS_API}/repos/${REPO}/releases/latest" "${WORK}/latest.json")"
    case "${code}" in
    200) LAST="$(python3 - "${WORK}/latest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    print(json.load(fh).get("tag_name", ""))
PY
)" ;;
    404) LAST="" ;;
    *) err "读取 latest 失败: HTTP ${code}"; return 1 ;;
    esac
}

stage_of()
{
    if [ "$2" = true ]; then
        printf 'draft\n'
        return 0
    fi
    if [ "$1" = true ]; then
        printf 'prerelease\n'
        return 0
    fi
    last_tag || return 1
    if [ "${LAST}" = "${TAG}" ]; then
        printf 'latest\n'
    else
        printf 'held\n'
    fi
}

# 读页面并把字段放进 REL_*。draft 不是本流水线会产生的状态，出现即说明
# 有人手建了页面，不能当成 held 放过去。
seek()
{
    local rc
    rel_get "${WORK}/rel.json"
    rc=$?
    case "${rc}" in
    0) ;;
    "${ENOREL}") err "Release ${TAG} 不存在，先完成发布再定阶段"; exit "${ENOREL}" ;;
    *) exit 1 ;;
    esac
    read -r REL_ID REL_PRE REL_DRAFT REL_TAG < <(rel_fields "${WORK}/rel.json")
    # 接口按 tag 查页面，返回的 tag_name 必须就是我们问的那一个：查到的页面
    # 若不是本 tag，后面所有 PATCH 都会打在别人的页面上。
    if [ "${REL_TAG}" != "${TAG}" ]; then
        err "页面标签与请求不符: ${REL_TAG} != ${TAG}"
        exit 1
    fi
}

# PATCH 是部分更新，因此只发要改的字段：hold 不动 prerelease 位，promote
# 必须把 prerelease 显式置 false，否则正式版会带着预发布标记成为 latest。
set_stage()
{
    local pre="$1" make="$2" id="$3" body="${WORK}/patch.json" code
    printf '{"prerelease":%s,"make_latest":"%s"}\n' "${pre}" "${make}" >"${body}"
    code="$(airy_assets_api PATCH \
        "${AIRY_ASSETS_API}/repos/${REPO}/releases/${id}" \
        "${WORK}/patched.json" "${body}")"
    [ "${code}" = 200 ] || { err "更新 Release ${TAG} 阶段失败: HTTP ${code}"; return 1; }
}

do_hold()
{
    local stage
    seek
    [ "${REL_DRAFT}" = true ] && { err "draft 页面不能定阶段: ${TAG}"; exit 1; }
    set_stage "${REL_PRE}" false "${REL_ID}" || exit 1
    seek
    stage="$(stage_of "${REL_PRE}" "${REL_DRAFT}")" || exit 1
    if [ "${stage}" = latest ]; then
        err "hold 之后仍在 latest: ${TAG}"
        exit 1
    fi
    say "阶段=${stage}（未进 latest）: ${TAG}"
}

do_promote()
{
    local stage
    case "${TAG}" in
    *-rc*|*-beta*) err "预发布不能置 latest: ${TAG}"; exit "${EINVAL}" ;;
    esac
    [ -f "${EVIDENCE}" ] || { err "证据文件不存在: ${EVIDENCE}"; exit "${EINVAL}"; }
    grep -q 'AIRYMAX-SMOKE-PASS' "${EVIDENCE}" || {
        err "证据里没有 AIRYMAX-SMOKE-PASS，B 轮验收未通过: ${EVIDENCE}"
        exit 1
    }
    seek
    [ "${REL_DRAFT}" = true ] && { err "draft 页面不能 promote: ${TAG}"; exit 1; }
    set_stage false true "${REL_ID}" || exit 1
    seek
    stage="$(stage_of "${REL_PRE}" "${REL_DRAFT}")" || exit 1
    if [ "${stage}" != latest ]; then
        err "promote 之后仍不是 latest（阶段=${stage}）: ${TAG}"
        exit 1
    fi
    say "阶段=latest（证据 ${EVIDENCE}）: ${TAG}"
}

do_check()
{
    local stage
    seek
    stage="$(stage_of "${REL_PRE}" "${REL_DRAFT}")" || exit 1
    if [ -z "${EXPECT}" ]; then
        say "阶段=${stage}: ${TAG}"
        return 0
    fi
    if [ "${stage}" != "${EXPECT}" ]; then
        err "阶段不符: 期望 ${EXPECT}，实得 ${stage}（${TAG}）"
        exit 1
    fi
    say "阶段=${stage}（符合期望）: ${TAG}"
}

main()
{
    parse_args "$@"
    # 令牌先落定再校验参数：check_args 判断的是「写操作有没有钥匙」，
    # 只认 TOKEN 会让工作流里的 GITHUB_TOKEN 被误判成缺失。
    TOKEN="${TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
    export TOKEN
    check_args
    WORK="$(mktemp -d)"
    trap 'rm -rf "${WORK}"' EXIT
    case "${STAGE}" in
    hold)    do_hold ;;
    promote) do_promote ;;
    check)   do_check ;;
    esac
}

main "$@"
