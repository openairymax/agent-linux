#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/release_assets.sh — 发布页资产的期望集与实测集
#
# 被 release-prune.sh（写入前收回页面）与 release-audit.sh（写入后对账）共用。
# 两处若各算一遍期望集，剪枝按 A 集合删、对账按 B 集合判，页面就可能在两道
# 门禁都通过的情况下仍然不一致——这正是「同一条判据写两遍」的典型代价。
#
# 期望集不另立清单：以摘要清单 agent-linux-<ver>.sha256sum 为唯一事实源。
# 它由 release_manifest.py 从本次物料确定性生成，且已由 release-verify.sh
# 逐项重算过摘要，因此可以据此判定「谁属于本次发布」。
#
# 依赖调用方先 source ci/lib/log.sh，并设置 TOKEN（只读演练可为空）。
# ===========================================================================

# 资产名是字节串，判定必须按字节序：comm(1) 要求输入按当前 collation 排好，
# 而在 en_US 之类的排序规则里标点被忽略（`agent-linux-…` 与 `a.rpm` 的先后
# 会与字节序不同），两侧排序看似一致、comm 却报 not in sorted order 并给出
# 错误的差集。统一钉住 C，让「排序」与「比较」用同一套规则。
export LC_ALL=C

AIRY_ASSETS_EINVAL=5
# 探不到 Release 时的返回码。剪枝把它当「还没建，无需处理」，对账把它当
# 「发布页不存在，不能算发布完成」——同一个事实，两种处置。
AIRY_ASSETS_ENOREL=3
# 接口终点由库自己拥有：调用方各自拼 URL 时，环境变量名就成了隐式契约，
# 写错只是 404 而不是报错。GITHUB_API_URL 在 GHES 与本地演练时覆盖默认值。
AIRY_ASSETS_API="${GITHUB_API_URL:-https://api.github.com}"
# 上传走独立主机（GHES 为 <host>/api/uploads），与读接口不同源，故单独一处。
AIRY_ASSETS_UPLOAD="${GITHUB_UPLOADS_URL:-https://uploads.github.com}"

# 期望集：摘要清单登记的物料，扣除只经 ghcr 交付的 OCI 镜像包，再加清单
# 自身与 release notes 及其署名；channel.json 只在目录里存在时保留，因为它
# 由装配轮取回，预发布轮不产出。
#
# 两个易错点都在这里收口：
#   ① 清单里 `*.asc` 已是独立条目，不能再按「每个文件补一份 .asc」推导，
#      否则会推出 `x.rpm.asc.asc` 这种不存在的名字，发布页永远判缺件；
#   ② `oci-image-<tag>.tar` 是 ghcr 的载荷，Release 页面因单资产 2 GiB 上限
#      不收它，清单却按「本次交付物」收录，两者口径不同，扣除规则属于本库。
airy_asset_want()
{
    local dir="$1" out="$2" sums n ver
    sums="$(find "${dir}" -maxdepth 1 -type f -name 'agent-linux-*.sha256sum' | sort)"
    if [ -z "${sums}" ]; then
        err "未找到摘要清单 agent-linux-*.sha256sum"
        return "${AIRY_ASSETS_EINVAL}"
    fi
    n="$(printf '%s\n' "${sums}" | wc -l)"
    if [ "${n}" -ne 1 ]; then
        err "摘要清单应恰好一份，实得 ${n}"
        return "${AIRY_ASSETS_EINVAL}"
    fi
    ver="${sums##*/}"
    ver="${ver#agent-linux-}"
    ver="${ver%.sha256sum}"
    {
        printf '%s\n' \
            "${sums##*/}" "${sums##*/}.asc" \
            "agent-linux-${ver}-release-notes.md" \
            "agent-linux-${ver}-release-notes.md.asc"
        if [ -f "${dir}/channel.json" ]; then
            printf 'channel.json\n'
        fi
        awk 'NF >= 2 && $2 !~ /^oci-image-/ { print $2 }' "${sums}"
    } | sort -u >"${out}"
}

# 只读演练允许无令牌跑公开仓库：没有令牌就不发 Authorization 头，
# 而不是发一个空的 Bearer（后者会被 401 挡回，把演练变成伪失败）。
#
# 鉴权参数由 airy_auth 统一刷新：库内已有四条路径（读 JSON、删资产、传资产、
# 取字节），各拼一次「谁带令牌」就变成四份实现，其中一份迟早会漏。
AIRY_AUTH=()

airy_auth()
{
    AIRY_AUTH=()
    if [ -n "${TOKEN:-}" ]; then
        AIRY_AUTH=(-H "Authorization: Bearer ${TOKEN}")
    fi
    return 0
}

# 第四个参数是写操作的请求体文件：JSON 走文件而不是命令行，免得引号在
# 两层解析里各转义一次。读与写共用同一处鉴权与版本头——「谁带了令牌」
# 若分成两份实现，其中一份迟早会忘。
airy_assets_api()
{
    local method="$1" url="$2" out="$3" body="${4:-}"
    local args=(-sS -X "${method}" -o "${out}" -w '%{http_code}')

    airy_auth
    if [ "${#AIRY_AUTH[@]}" -gt 0 ]; then
        args+=("${AIRY_AUTH[@]}")
    fi
    args+=(-H 'Accept: application/vnd.github+json'
           -H 'X-GitHub-Api-Version: 2022-11-28')
    if [ -n "${body}" ]; then
        args+=(-H 'Content-Type: application/json' --data-binary "@${body}")
    fi
    curl "${args[@]}" "${url}"
}

# airy_asset_del REPO ID —— 收回一件资产。404 视为已不在页面：调用方要的
# 事实是「这件资产不再挂在这里」，而不是「删除请求曾经成功过」。
airy_asset_del()
{
    local repo="$1" id="$2" code

    code="$(airy_assets_api DELETE \
        "${AIRY_ASSETS_API}/repos/${repo}/releases/assets/${id}" /dev/null)"
    case "${code}" in
        200|204|404) return 0 ;;
    esac
    err "删除资产 ${id} 失败: HTTP ${code}"
    return 1
}

# airy_asset_put REPO RID FILE NAME OUT —— 上传一件资产，201 时资产 JSON 落 OUT。
# Release 不接受同名覆盖（422 只报 already_exists），替换必须先由调用方删旧。
airy_asset_put()
{
    local repo="$1" rid="$2" file="$3" name="$4" out="$5" code

    airy_auth
    code="$(curl -sS -X POST -o "${out}" -w '%{http_code}' \
        ${AIRY_AUTH[@]+"${AIRY_AUTH[@]}"} \
        -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' \
        -H 'Content-Type: application/octet-stream' \
        --data-binary "@${file}" \
        "${AIRY_ASSETS_UPLOAD}/repos/${repo}/releases/${rid}/assets?name=${name}")"
    if [ "${code}" != 201 ]; then
        err "上传 ${name} 失败: HTTP ${code}"
        return 1
    fi
    return 0
}

# airy_asset_get REPO ID OUT —— 按资产 id 取回原始字节。跟随 302 到对象存储：
# curl -L 不会把 Authorization 带到别的域，令牌不外泄。
airy_asset_get()
{
    local repo="$1" id="$2" out="$3" code

    airy_auth
    code="$(curl -sSL -o "${out}" -w '%{http_code}' \
        ${AIRY_AUTH[@]+"${AIRY_AUTH[@]}"} \
        -H 'Accept: application/octet-stream' \
        "${AIRY_ASSETS_API}/repos/${repo}/releases/assets/${id}")"
    if [ "${code}" != 200 ]; then
        err "取回资产 ${id} 失败: HTTP ${code}"
        return 1
    fi
    return 0
}

# 实测集：`<asset id> <name>` 每行一条。Release 不存在返回 ENOREL，
# 其余接口失败返回 1（调用方据此区分「没有页面」与「页面读不到」）。
airy_asset_have()
{
    local repo="$1" tag="$2" out="$3" rel="$4" code
    code="$(airy_assets_api GET \
        "${AIRY_ASSETS_API}/repos/${repo}/releases/tags/${tag}" "${rel}")"
    case "${code}" in
        200) ;;
        404) return "${AIRY_ASSETS_ENOREL}" ;;
        *)
            err "读取 Release ${tag} 失败: HTTP ${code}"
            return 1
            ;;
    esac
    python3 - "${rel}" >"${out}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    release = json.load(fh)
for asset in release.get("assets", []):
    print(asset["id"], asset["name"])
PY
}
