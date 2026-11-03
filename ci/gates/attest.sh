#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/attest.sh — 给 ISO 挂构建来源证明（in-toto / SLSA v1）
#
# 用法: ci/gates/attest.sh build   --out DIR --tag TAG [--iso-rel REL]
#                                  [--manifest FILE] [--ref REPO]
#       ci/gates/attest.sh publish --out DIR --tag TAG [--iso-rel REL]
#                                  [--ref REPO] [--token TOKEN] [--force]
#       ci/gates/attest.sh verify  --file FILE [--iso FILE] [--build-id ID]
#
# 镜像带着 BUILD_ID 装到机器上，只能回答「我是哪次构建」，回答不了「这次
# 构建从哪个提交、由哪条流水线、产出的是不是这盘 ISO」。前者是运行期自述，
# 可以伪造；后者必须由构建方在产物之外单独签名成证。本 gate 生成并发布这
# 份证明，让「装机后反查来源」从一句承诺变成一条可复算的记录（CI-17）。
#
# 证明的取值口径：
#   * buildId 取自镜像自带的构建清单（image-ci-manifest.txt），不是重新推导。
#     重新推导会得到「本 gate 认为应该是的值」，与镜像内实际写入的值一旦
#     分叉，证明就会替错误的镜像背书。清单缺失时才退回 buildid.sh 复算。
#   * subject 是本 gate 现算的 ISO sha256，并与发行方写出的 .sha256 对账；
#     两者不一致说明 ISO 在发布账本之后被改过，此时拒绝出证。
#   * 时间戳一律不写。同一输入重跑必须得到同一份字节，否则幂等判断失效，
#     每次重跑都会把证明刷成「与上次不同」而要求 --force。
#
# 发布是内容寻址的：证明以 attest-<tag> 这个固定标签推入 ISO 所在仓库，
# 因此 oras manifest fetch <repo>:attest-<tag> 就能看到它，不需要先知道
# 镜像摘要。推后回读比对摘要与字节，任一处不符即失败，不留半份证明。
#
# 退出码: 0 成功；1 生成、校验、推送或回读失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly ISO_REL_DEFAULT="desktop/iso"
readonly REF_DEFAULT="ghcr.io/openairymax/airymaxos-iso"
readonly ATTEST_TYPE="application/vnd.in-toto+json"
readonly ANN="org.openairymax.attest"

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly ROOT
readonly BUILDID="${ROOT}/distro/scripts/lib/buildid.sh"
# shellcheck source=../../distro/scripts/lib/buildid.sh
. "${BUILDID}"

CMD=""
OUT=""
TAG=""
ISO_REL="${ISO_REL_DEFAULT}"
MANIFEST=""
REF="${REF_DEFAULT}"
TOKEN=""
FORCE=0
FILE=""
ISO=""
BID=""

usage()
{
    echo "用法: ci/gates/attest.sh build   --out DIR --tag TAG [--iso-rel REL] [--manifest FILE] [--ref REPO]"
    echo "      ci/gates/attest.sh publish --out DIR --tag TAG [--iso-rel REL] [--ref REPO] [--token TOKEN] [--force]"
    echo "      ci/gates/attest.sh verify  --file FILE [--iso FILE] [--build-id ID]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        build|publish|verify) CMD="$1"; shift ;;
        --out)      OUT="${2:-}"; shift 2 ;;
        --tag)      TAG="${2:-}"; shift 2 ;;
        --iso-rel)  ISO_REL="${2:-}"; shift 2 ;;
        --manifest) MANIFEST="${2:-}"; shift 2 ;;
        --ref)      REF="${2:-}"; shift 2 ;;
        --token)    TOKEN="${2:-}"; shift 2 ;;
        --force)    FORCE=1; shift ;;
        --file)     FILE="${2:-}"; shift 2 ;;
        --iso)      ISO="${2:-}"; shift 2 ;;
        --build-id) BID="${2:-}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${CMD}" ] || { usage >&2; exit "${EINVAL}"; }

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

att_dir()
{
    local dir="${OUT}/${ISO_REL}" n
    [ -d "${dir}" ] || die "镜像目录不存在: ${dir}"
    n="$(find "${dir}" -maxdepth 1 -name '*.iso' -type f | wc -l)"
    [ "${n}" -eq 1 ] || die "期望恰好一个 ISO，实际 ${n} 个: ${dir}"
    find "${dir}" -maxdepth 1 -name '*.iso' -type f
}

# 指纹优先取镜像自带清单：那是写进 /etc/airymaxos-build 的原值，证明与装机
# 后的反查正是靠它对齐。清单里的 unset 是发布链路漏注入的征兆，不能拿去出证。
att_bid()
{
    local id=""
    if [ -n "${MANIFEST}" ]; then
        [ -f "${MANIFEST}" ] || die "清单不存在: ${MANIFEST}"
        id="$(sed -n 's/^build_id=//p' "${MANIFEST}" | head -1)"
        [ -n "${id}" ] || die "清单缺 build_id: ${MANIFEST}"
    elif [ -f "${OUT}/image-ci-manifest.txt" ]; then
        id="$(sed -n 's/^build_id=//p' "${OUT}/image-ci-manifest.txt" | head -1)"
    fi
    [ -n "${id}" ] || id="$(airy_build_id "${ROOT}/distro")"
    airy_bid_ok "${id}" || die "构建指纹不合法: ${id}"
    printf '%s\n' "${id}"
}

att_gen()
{
    local iso bid commit file
    [ -n "${OUT}" ] && [ -n "${TAG}" ] || { usage >&2; exit "${EINVAL}"; }
    need_cmd python3 sha256sum git
    iso="$(att_dir)"
    bid="$(att_bid)"
    commit="$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null)"
    [ -n "${commit}" ] || die "无法确定源码提交，拒绝出证"
    file="${iso%/*}/attest-${TAG}.json"
    python3 - "${file}" "${iso}" "${TAG}" "${bid}" "${ISO_REL}" "${commit}" <<'PY' ||
import hashlib, json, os, sys

out, iso, tag, bid, rel, commit = sys.argv[1:7]
h = hashlib.sha256()
with open(iso, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
repo = os.environ.get("GITHUB_REPOSITORY") or "openairymax/agent-linux"
server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
run = os.environ.get("GITHUB_RUN_ID") or ""
attempt = os.environ.get("GITHUB_RUN_ATTEMPT") or "1"
ref = os.environ.get("GITHUB_REF") or ("refs/tags/" + tag)
builder = (server + "/" + repo + "/actions/runs/" + run) if run \
    else "local://ci/gates/attest.sh"
inv = (repo + "/actions/runs/" + run + "/attempts/" + attempt) if run \
    else "local"
st = {
    "_type": "https://in-toto.io/Statement/v1",
    "subject": [{"name": os.path.basename(iso),
                 "digest": {"sha256": h.hexdigest()}}],
    "predicateType": "https://slsa.dev/provenance/v1",
    "predicate": {
        "buildDefinition": {
            "buildType": "https://openairymax.org/buildtypes/oci-iso/v1",
            "externalParameters": {"tag": tag, "arch": "x86_64",
                                   "image": os.path.basename(iso),
                                   "size": os.path.getsize(iso),
                                   "mediaRel": rel + "/" + os.path.basename(iso)},
            "internalParameters": {"buildId": bid, "commit": commit,
                                   "repository": repo, "runId": run,
                                   "runAttempt": attempt, "ref": ref},
            "resolvedDependencies": [{
                "uri": "git+" + server + "/" + repo + "@" + commit,
                "digest": {"gitCommit": commit}}],
        },
        "runDetails": {"builder": {"id": builder},
                       "metadata": {"invocationId": inv}},
    },
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(st, fh, sort_keys=True, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
        die "生成证明失败: ${file}"
    att_check "${file}" "${iso}" "${bid}" "${iso}.sha256" ||
        die "生成后自检失败: ${file}"
    say "证明已生成: ${file}"
}

# 校验器同时服务三处：生成后自检、发布前复核、装机侧反查。任何一处走不同
# 实现都会让「证明有效」出现两种口径，所以只有这一个 python 块。
att_check()
{
    python3 - "$@" <<'PY'
import hashlib, json, os, re, sys

path, iso, bid, shafile = (sys.argv[1:5] + ["", "", "", ""])[:4]
errs = []
try:
    with open(path, encoding="utf-8") as fh:
        st = json.load(fh)
except Exception as exc:  # 文件缺失或不是 JSON 都属于「证明无效」
    print("证明不可读: %s (%s)" % (path, exc), file=sys.stderr)
    sys.exit(1)

if st.get("_type") != "https://in-toto.io/Statement/v1":
    errs.append("_type 不是 in-toto Statement v1")
if st.get("predicateType") != "https://slsa.dev/provenance/v1":
    errs.append("predicateType 不是 SLSA v1")
sub = st.get("subject") or []
if len(sub) != 1:
    errs.append("subject 应恰有一项，实际 %d" % len(sub))
    sub = sub[:1] or [{}]
digest = ((sub[0].get("digest") or {}).get("sha256") or "")
if not re.fullmatch(r"[0-9a-f]{64}", digest):
    errs.append("subject 缺合法 sha256")
name = sub[0].get("name") or ""
if iso:
    h = hashlib.sha256()
    with open(iso, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != digest:
        errs.append("ISO 摘要与证明不符")
if shafile and os.path.exists(shafile):
    want = open(shafile, encoding="utf-8").read().split()[0]
    if want != digest:
        errs.append("发行账本摘要与证明不符")
ib = (((st.get("predicate") or {}).get("buildDefinition") or {})
      .get("internalParameters") or {})
got = ib.get("buildId") or ""
if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", got) or got == "unset":
    errs.append("buildId 非法: %r" % got)
if bid and got != bid:
    errs.append("buildId 不符: 期望 %s，证明为 %s" % (bid, got))
if not ib.get("commit"):
    errs.append("缺少源码提交")

if errs:
    for e in errs:
        print("证明无效: " + e, file=sys.stderr)
    sys.exit(1)
print("AIRYMAX-ATTEST-PASS buildId=%s subject=%s name=%s" % (got, digest, name))
PY
}

att_login()
{
    [ -n "${TOKEN}" ] || return 0
    printf '%s\n' "${TOKEN}" |
        oras login "${REF%%/*}" --username airymax --password-stdin \
        >/dev/null 2>&1 || die "oras 登录失败: ${REF%%/*}"
}

# 取回远端清单到 WORK/m.json。取不回即返回 1：调用方据此区分「尚未发布」
# 与「发布过但读不出来」，后者的正确处理是失败而不是覆盖。
att_fetch()
{
    oras manifest fetch "$1" >"${WORK}/m.json" 2>"${WORK}/m.log"
}

att_ann()
{
    python3 - "${WORK}/m.json" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    print((json.load(fh).get("annotations") or {}).get(sys.argv[2], ""))
PY
}

att_pub()
{
    local file sum sub bid target have
    [ -n "${OUT}" ] && [ -n "${TAG}" ] || { usage >&2; exit "${EINVAL}"; }
    need_cmd oras python3 sha256sum
    file="${OUT}/${ISO_REL}/attest-${TAG}.json"
    [ -s "${file}" ] || die "缺少证明，先运行 build: ${file}"
    sum="$(sha256sum "${file}" | cut -d' ' -f1)"
    { read -r sub; read -r bid; } < <(python3 - "${file}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    st = json.load(fh)
ib = st["predicate"]["buildDefinition"]["internalParameters"]
print(st["subject"][0]["digest"]["sha256"])
print(ib["buildId"])
PY
    )
    [ -n "${bid}" ] || die "证明缺 buildId: ${file}"
    att_login
    target="${REF}:attest-${TAG}"
    if att_fetch "${target}"; then
        have="$(att_ann "${ANN}.sha256")"
        if [ "${have}" = "${sum}" ] && [ "${FORCE}" -eq 0 ]; then
            say "同一份证明已发布，跳过: ${target}"
            return 0
        fi
        [ "${FORCE}" -eq 1 ] ||
            die "已存在不同证明（sha256=${have:-空}）；确认替换才加 --force"
    fi
    ( cd "${file%/*}" && oras push --artifact-type "${ATTEST_TYPE}" \
        --annotation "${ANN}.sha256=${sum}" \
        --annotation "${ANN}.build-id=${bid}" \
        --annotation "${ANN}.subject-sha256=${sub}" \
        --annotation "${ANN}.tag=${TAG}" \
        "${target}" "${file##*/}:${ATTEST_TYPE}" ) >"${WORK}/push.log" 2>&1 ||
        die "推送证明失败: ${target}"
    att_fetch "${target}" || die "推送后取不回证明: ${target}"
    [ "$(att_ann "${ANN}.sha256")" = "${sum}" ] || die "回读摘要与本地不一致"
    [ "$(att_ann "${ANN}.build-id")" = "${bid}" ] || die "回读 buildId 与本地不一致"
    mkdir -p "${WORK}/back"
    oras pull "${target}" -o "${WORK}/back" >/dev/null 2>&1 ||
        die "取回证明失败: ${target}"
    cmp -s "${file}" "${WORK}/back/${file##*/}" ||
        die "取回字节与本地不一致: ${target}"
    say "证明已发布并回读一致: ${target}"
}

att_ver()
{
    [ -n "${FILE}" ] || { usage >&2; exit "${EINVAL}"; }
    need_cmd python3 sha256sum
    [ -f "${FILE}" ] || die "证明不存在: ${FILE}"
    att_check "${FILE}" "${ISO}" "${BID}" "" ||
        die "证明校验失败: ${FILE}"
}

case "${CMD}" in
    build)   att_gen ;;
    publish) att_pub ;;
    verify)  att_ver ;;
esac
