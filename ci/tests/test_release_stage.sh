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
# make_latest 是互斥指针：置 true 让本页成为 latest，置 false 则 latest 退回
# 「无」。桩若不跟着改写 latest.json，门禁读回的永远是上一次的页面，
# 「hold 之后是否真的退出了 latest」就永远测不出来。
flag = str(patch.get("make_latest"))
if flag == "true":
    with open(os.path.join(root, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(rel, fh)
elif flag == "false":
    with open(os.path.join(root, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump({"tag_name": ""}, fh)
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

reset()
{
    : >"${STUB_LOG}"
    rm -f "${DIR}/patch-body.json"
    STUB_CODE_TAG=200
    STUB_CODE_LATEST=200
    STUB_CODE_PATCH=200
    TOKEN="test-token"
}

run()
{
    out="$("${FX}/ci/gates/release-stage.sh" "$@" 2>&1)"
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
reset; page false false "${TAG}"; latest "${TAG}"
run --tag "${TAG}" --repo o/r --stage hold
check "hold 收回 make_latest" 0 "阶段=held"
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
