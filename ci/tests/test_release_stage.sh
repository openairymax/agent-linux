#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_release_stage.sh — 发布阶段门禁的分支回归
#
# release-stage.sh 判的是「这个页面能不能被装机收到」，而它的每一处误判
# 都表现为页面照常存在：漏了 hold 就等于把未验收的构建推成 latest，漏了
# 读回就等于 PATCH 静默失败而门禁报成功。因此 curl 用桩顶替，桩同时扮演
# 仓库：GET 取夹具，PATCH 真的改夹具，门禁随后读回的就是改过的页面。
#
# 用法: bash ci/tests/test_release_stage.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
DIR="${tmp}/repo"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${DIR}" "${tmp}/stub"
cp "${ROOT}/ci/gates/release-stage.sh" "${FX}/ci/gates/release-stage.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/release-assets.sh" "${FX}/ci/lib/release-assets.sh"

# 桩把三种请求分开处理：tags 详情、latest、PATCH。PATCH 真的改写夹具里的
# 字段并把 make_latest 透传给 latest.json，这样「门禁有没有读回」才有意义。
cat >"${tmp}/stub/curl" <<'EOF'
#!/usr/bin/env bash
method=GET; url=""; out=""
while [ "$#" -gt 0 ]; do
    case "$1" in
    -X) method="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    -w) shift 2 ;;
    --data-binary) body="${2#@}"; shift 2 ;;
    -H) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
    esac
done
printf '%s %s\n' "${method}" "${url}" >>"${STUB_LOG}"
case "${url}" in
*/releases/latest)
    cp "${STUB_DIR}/latest.json" "${out}"
    printf '%s' "${STUB_CODE_LATEST}"
    ;;
*/releases/tags/*)
    cp "${STUB_DIR}/rel.json" "${out}"
    printf '%s' "${STUB_CODE_TAG}"
    ;;
*/releases?per_page=*)
    cp "${STUB_DIR}/list.json" "${out}"
    printf '%s' "${STUB_CODE_LIST}"
    ;;
*/releases/202)
    cp "${STUB_DIR}/other.json" "${out}"
    if [ "${method}" = PATCH ] && [ "${STUB_CODE_PATCH}" = 200 ]; then
        cp "${body}" "${STUB_DIR}/patch-other.json"
        python3 - "${STUB_DIR}" "${body}" <<'PY'
import json
import os
import sys

root, body_path = sys.argv[1], sys.argv[2]
with open(os.path.join(root, "other.json"), encoding="utf-8") as fh:
    rel = json.load(fh)
with open(body_path, encoding="utf-8") as fh:
    patch = json.load(fh)
if "prerelease" in patch:
    rel["prerelease"] = bool(patch["prerelease"])
    with open(os.path.join(root, "other.json"), "w", encoding="utf-8") as fh:
        json.dump(rel, fh)
if str(patch.get("make_latest")) == "true":
    with open(os.path.join(root, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(rel, fh)
PY
    fi
    printf '%s' "${STUB_CODE_PATCH}"
    ;;
*/releases/[0-9]*)
    cp "${body}" "${STUB_DIR}/patch-body.json"
    if [ "${STUB_CODE_PATCH}" = 200 ]; then
        python3 - "${STUB_DIR}" "${body}" <<'PY'
import json
import os
import sys

root, body_path = sys.argv[1], sys.argv[2]
with open(os.path.join(root, "rel.json"), encoding="utf-8") as fh:
    rel = json.load(fh)
with open(body_path, encoding="utf-8") as fh:
    patch = json.load(fh)
if "prerelease" in patch:
    rel["prerelease"] = bool(patch["prerelease"])
    with open(os.path.join(root, "rel.json"), "w", encoding="utf-8") as fh:
        json.dump(rel, fh)
# 与真实接口同判：make_latest=false 只影响建页，对已置 latest 的页面是空
# 操作（2026-11-17 实测 70 秒后读回仍是本页）。桩若在这里把 latest 清空，
# 「hold 靠什么退出 latest」就永远测不到真实语义。
if str(patch.get("make_latest")) == "true":
    with open(os.path.join(root, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(rel, fh)
PY
        cp "${STUB_DIR}/rel.json" "${out}"
    fi
    printf '%s' "${STUB_CODE_PATCH}"
    ;;
*)
    printf '404'
    ;;
esac
EOF
chmod +x "${tmp}/stub/curl"

export PATH="${tmp}/stub:${PATH}"
export GITHUB_API_URL="https://api.example"
export STUB_DIR="${DIR}" STUB_LOG="${tmp}/requests"
export TOKEN="test-token"
export STUB_CODE_TAG=200 STUB_CODE_LATEST=200 STUB_CODE_PATCH=200
export STUB_CODE_LIST=200
: >"${STUB_LOG}"

TAG="v0.0.4"

# 页面夹具：id 取固定值，便于在请求日志里认出 PATCH 打到了哪个对象。
page()
{
    local pre="$1" draft="$2" tag="$3"
    printf '{"id":101,"prerelease":%s,"draft":%s,"tag_name":"%s"}\n' \
        "${pre}" "${draft}" "${tag}" >"${DIR}/rel.json"
}

latest()
{
    printf '{"tag_name":"%s"}\n' "$1" >"${DIR}/latest.json"
}

# 可承接 latest 的回退页：id 202。list.json 只列这一页，除非用例另外覆盖。
back()
{
    printf '{"id":202,"prerelease":false,"draft":false,"tag_name":"%s","published_at":"%s"}\n' \
        "$1" "$2" >"${DIR}/other.json"
    printf '[%s]\n' "$(cat "${DIR}/other.json")" >"${DIR}/list.json"
}

reset()
{
    : >"${STUB_LOG}"
    rm -f "${DIR}/patch-body.json" "${DIR}/patch-other.json"
    STUB_CODE_TAG=200
    STUB_CODE_LATEST=200
    STUB_CODE_PATCH=200
    STUB_CODE_LIST=200
    printf '[]\n' >"${DIR}/list.json"
    TOKEN="test-token"
}

# 门禁的令牌来源是 TOKEN → GITHUB_TOKEN → GH_TOKEN 的回落链，"缺令牌"这一
# 分支只在整条链都空时成立。开发机 shell 可能带着 GH_TOKEN、CI 带着
# GITHUB_TOKEN，不隔离就让同一条用例在同一份代码上时红时绿；因此子进程只
# 拿到本脚本显式给定的 TOKEN。
run()
{
    out="$(env -u GITHUB_TOKEN -u GH_TOKEN TOKEN="${TOKEN-}" \
        "${FX}/ci/gates/release-stage.sh" "$@" 2>&1)"
    rc=$?
}

check()
{
    local name="$1" want="$2" pat="${3:-}"
    if [ "${rc}" -ne "${want}" ]; then
        printf 'FAIL %s: 期望退出 %s，实得 %s\n%s\n' \
            "${name}" "${want}" "${rc}" "${out}"
        fails=$((fails + 1))
        return 0
    fi
    if [ -n "${pat}" ] && ! printf '%s' "${out}" | grep -qF -- "${pat}"; then
        printf 'FAIL %s: 输出缺少 "%s"\n%s\n' "${name}" "${pat}" "${out}"
        fails=$((fails + 1))
        return 0
    fi
    printf 'ok   %s\n' "${name}"
}

# --- 只读阶段判定 ---------------------------------------------------------
reset; page false false "${TAG}"; latest v0.0.3
run --tag "${TAG}" --repo o/r --expect held
check "正式页尚未置 latest 时为 held" 0 "阶段=held"

run --tag "${TAG}" --repo o/r --expect latest
check "错当下 latest 时判负" 1 "阶段不符"

reset; page false false "${TAG}"; latest "${TAG}"
run --tag "${TAG}" --repo o/r --expect latest
check "已是 latest 时 check 通过" 0 "阶段=latest"

reset; page true false "${TAG}-rc1"; latest v0.0.3
run --tag "${TAG}-rc1" --repo o/r
check "不带 --expect 只打印阶段" 0 "阶段=prerelease"

reset; page false false "${TAG}"; latest v0.0.3; STUB_CODE_TAG=404
run --tag "${TAG}" --repo o/r --stage hold
check "页面不存在退 3" 3 "不存在"

reset; page false false "v0.0.3"; latest v0.0.3
run --tag "${TAG}" --repo o/r --stage hold
check "页面标签与请求不符判负" 1 "标签与请求不符"
if grep -q "^PATCH" "${STUB_LOG}"; then
    printf 'FAIL 标签不符仍改写了页面\n'
    fails=$((fails + 1))
else
    printf 'ok   标签不符时不写页面\n'
fi

# --- hold ----------------------------------------------------------------
# 已在 latest 的页面靠 make_latest=false 退不出去（真实接口实测），hold 必须
# 把 latest 指给另一个稳定版；这一条同时钉住「退出」与「交给谁」两件事。
reset; page false false "${TAG}"; latest "${TAG}"; back v0.0.3 2026-11-01T00:00:00Z
run --tag "${TAG}" --repo o/r --stage hold
check "hold 把 latest 交给上一稳定版" 0 "latest 回退到 v0.0.3"
if grep -q "PATCH https://api.example/repos/o/r/releases/101" "${STUB_LOG}"; then
    printf 'ok   hold 打到页面对象 101\n'
else
    printf 'FAIL hold 未 PATCH 页面对象\n%s\n' "$(cat "${STUB_LOG}")"
    fails=$((fails + 1))
fi
if grep -qF '"prerelease":false,"make_latest":"false"' "${DIR}/patch-body.json"; then
    printf 'ok   hold 请求体只收 make_latest\n'
else
    printf 'FAIL hold 请求体不符: %s\n' "$(cat "${DIR}/patch-body.json")"
    fails=$((fails + 1))
fi
if grep -q "PATCH https://api.example/repos/o/r/releases/202" "${STUB_LOG}" &&
    grep -qF '{"make_latest":"true"}' "${DIR}/patch-other.json"; then
    printf 'ok   hold 只改回退页的 latest 指针\n'
else
    printf 'FAIL hold 未正确改回退页: %s %s\n' \
        "$(cat "${STUB_LOG}")" "$(cat "${DIR}/patch-other.json")"
    fails=$((fails + 1))
fi
if grep -qF '"tag_name": "v0.0.3"' "${DIR}/latest.json"; then
    printf 'ok   latest 已落到回退页\n'
else
    printf 'FAIL latest 未落到回退页: %s\n' "$(cat "${DIR}/latest.json")"
    fails=$((fails + 1))
fi

reset; page false false "${TAG}"; latest "${TAG}"
run --tag "${TAG}" --repo o/r --stage hold
check "无其他稳定版可承接时 hold 判负" 1 "没有其他稳定版"

# draft、prerelease 与本页都不能承接 latest，列表里只有它们时结果与空列表同。
reset; page false false "${TAG}"; latest "${TAG}"
printf '[{"id":101,"prerelease":false,"draft":false,"tag_name":"%s","published_at":"2026-11-17T00:00:00Z"},{"id":303,"prerelease":true,"draft":false,"tag_name":"v0.0.5-rc1","published_at":"2026-11-17T01:00:00Z"},{"id":404,"prerelease":false,"draft":true,"tag_name":"v0.0.6","published_at":"2026-11-17T02:00:00Z"}]\n' "${TAG}" >"${DIR}/list.json"
run --tag "${TAG}" --repo o/r --stage hold
check "draft 与 prerelease 不作回退目标" 1 "没有其他稳定版"

reset; page false false "${TAG}"; latest "${TAG}"; STUB_CODE_LIST=500
run --tag "${TAG}" --repo o/r --stage hold
check "发布列表不可读时 hold 判负" 1 "读取发布列表失败"

reset; page true false "${TAG}-rc1"; latest v0.0.3
run --tag "${TAG}-rc1" --repo o/r --stage hold
check "hold 不清预发布位" 0 "阶段=prerelease"
if grep -qF '"prerelease":true' "${DIR}/patch-body.json"; then
    printf 'ok   hold 保留 prerelease\n'
else
    printf 'FAIL hold 改动了 prerelease: %s\n' "$(cat "${DIR}/patch-body.json")"
    fails=$((fails + 1))
fi

reset; page false false "${TAG}"; latest "${TAG}"; STUB_CODE_PATCH=500
run --tag "${TAG}" --repo o/r --stage hold
check "PATCH 失败判负" 1 "阶段失败"

# --- promote -------------------------------------------------------------
reset; page false false "${TAG}"; latest v0.0.3
printf 'not ok\n' >"${tmp}/tap.txt"
run --tag "${TAG}" --repo o/r --stage promote
check "promote 缺证据退 5" 5 "必须给 --evidence"

run --tag "${TAG}" --repo o/r --stage promote --evidence "${tmp}/tap.txt"
check "证据不含 SMOKE-PASS 判负" 1 "AIRYMAX-SMOKE-PASS"
if grep -q "^PATCH" "${STUB_LOG}"; then
    printf 'FAIL 证据不足仍改写了页面\n'
    fails=$((fails + 1))
else
    printf 'ok   证据不足时不写页面\n'
fi

reset; page false false "${TAG}"; latest v0.0.3
printf 'ok 1 - desktop\nAIRYMAX-SMOKE-PASS\n' >"${tmp}/tap.txt"
run --tag "${TAG}" --repo o/r --stage promote --evidence "${tmp}/tap.txt"
check "证据齐备时置 latest" 0 "阶段=latest"
if grep -qF '"prerelease":false,"make_latest":"true"' "${DIR}/patch-body.json"; then
    printf 'ok   promote 请求体同时清预发布位\n'
else
    printf 'FAIL promote 请求体不符: %s\n' "$(cat "${DIR}/patch-body.json")"
    fails=$((fails + 1))
fi

reset; page false false "${TAG}-rc2"; latest v0.0.3
run --tag "${TAG}-rc2" --repo o/r --stage promote --evidence "${tmp}/tap.txt"
check "预发布禁止 promote" 5 "-rc"

# --- 参数 ----------------------------------------------------------------
reset
run --repo o/r --stage check
check "缺 --tag 退 5" 5 "缺少 --tag"

run --tag "${TAG}" --repo o/r --stage bogus
check "未知阶段退 5" 5 "--stage"

run --tag "${TAG}" --repo o/r --expect nowhere
check "未知期望退 5" 5 "--expect"

reset; page false false "${TAG}"; latest v0.0.3; TOKEN=""
run --tag "${TAG}" --repo o/r --stage hold
check "写操作缺令牌退 5" 5 "TOKEN"

TOKEN=""
page false false "${TAG}"; latest v0.0.3
run --tag "${TAG}" --repo o/r
check "只读 check 允许匿名" 0 "阶段=held"

if [ "${fails}" -ne 0 ]; then
    printf '\n%d 项失败\n' "${fails}" >&2
    exit 1
fi
printf '\n全部通过\n'
