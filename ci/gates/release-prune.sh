#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-prune.sh — 让 Release 资产与本次物料严格一致
#
# 用法: ci/gates/release-prune.sh --tag TAG [--repo OWNER/NAME] [--dir DIR] [--dry-run]
#
# 建 Release 的动作用 glob 上传，只增不删。内核包名里带 RPM release 号
# （airymaxos-kernel-core-0.0.3-5.x86_64.rpm），同一个 tag 重发一轮就多出一整套
# 新号的包，旧号包原地留存：v0.0.3 首轮发布后页面上并排躺着 34 个 -5 的包，
# 取件方无从判断哪一套对应当前 tag。tag 重指是本项目的常规动作（release.yml
# 的 concurrency 就是为它设的），所以清理不是一次性收尾，而是发布流程必须
# 自带的幂等性。
#
# 期望集不另立清单：以摘要清单 agent-linux-<ver>.sha256sum 为唯一事实源。
# 它由 release_manifest.py 从本次物料确定性生成，且已由 release-verify.sh
# 逐项重算过摘要，因此可以放心据此判定「谁不属于本次发布」。
#
# 退出码: 0 一致或无可删；1 接口失败或删除失败；5 参数与前置条件不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

API="${GITHUB_API_URL:-https://api.github.com}"
REPO="${GITHUB_REPOSITORY:-}"
TAG=""
DIR="."
DRY=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)     TAG="${2:-}"; shift 2 ;;
        --repo)    REPO="${2:-}"; shift 2 ;;
        --dir)     DIR="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${TAG}" ] || { err "缺少 --tag"; exit "${EINVAL}"; }
[ -n "${REPO}" ] || { err "缺少 --repo（或 GITHUB_REPOSITORY）"; exit "${EINVAL}"; }
[ -d "${DIR}" ] || { err "目录不存在: ${DIR}"; exit "${EINVAL}"; }

TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [ -z "${TOKEN}" ] && [ "${DRY}" -eq 0 ]; then
    err "缺少 GITHUB_TOKEN（只读演练请加 --dry-run）"
    exit "${EINVAL}"
fi

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# 期望集：清单里出现的每个文件名，连同其署名；外加清单自身。
want_of()
{
    awk 'NF >= 2 { print $2; print $2 ".asc" }' "$1" | sort -u
}

sums="$(find "${DIR}" -maxdepth 1 -type f -name 'agent-linux-*.sha256sum' | sort)"
[ -n "${sums}" ] || { err "未找到摘要清单 agent-linux-*.sha256sum"; exit "${EINVAL}"; }
[ "$(printf '%s\n' "${sums}" | wc -l)" -eq 1 ] ||
    { err "摘要清单应恰好一份，实得 $(printf '%s\n' "${sums}" | wc -l)"; exit "${EINVAL}"; }

{
    printf '%s\n' "${sums##*/}"
    want_of "${sums}"
} | sort -u >"${work}/want"

# 只读演练允许无令牌跑公开仓库：没有令牌就不发 Authorization 头，
# 而不是发一个空的 Bearer（后者会被 401 挡回，把演练变成伪失败）。
call()
{
    local method="$1" url="$2" out="$3"
    local -a auth=()
    if [ -n "${TOKEN}" ]; then
        auth=(-H "Authorization: Bearer ${TOKEN}")
    fi
    curl -sS -X "${method}" -o "${out}" -w '%{http_code}' "${auth[@]}" \
        -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' \
        "${url}"
}

code="$(call GET "${API}/repos/${REPO}/releases/tags/${TAG}" "${work}/rel.json")"
case "${code}" in
    200) ;;
    404) say "Release ${TAG} 尚未建立，无需清理"; exit 0 ;;
    *)   err "读取 Release ${TAG} 失败: HTTP ${code}"; exit 1 ;;
esac

python3 - "${work}/rel.json" >"${work}/have" <<'PY'
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    release = json.load(fh)
for asset in release.get("assets", []):
    print(asset["id"], asset["name"])
PY

kept=0
gone=0
while read -r id name; do
    [ -n "${id:-}" ] || continue
    if grep -Fxq -- "${name}" "${work}/want"; then
        kept=$((kept + 1))
        continue
    fi
    if [ "${DRY}" -eq 1 ]; then
        say "待删除 ${name}"
        gone=$((gone + 1))
        continue
    fi
    code="$(call DELETE "${API}/repos/${REPO}/releases/assets/${id}" /dev/null)"
    [ "${code}" = "204" ] || { err "删除 ${name} 失败: HTTP ${code}"; exit 1; }
    say "已删除 ${name}"
    gone=$((gone + 1))
done <"${work}/have"

if [ "${DRY}" -eq 1 ]; then
    say "演练: 本次发布 ${kept} 项，陈旧 ${gone} 项（未改动，${TAG}）"
else
    say "本次发布 ${kept} 项，已清理陈旧 ${gone} 项（${TAG}）"
fi
