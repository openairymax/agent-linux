#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/attest.sh — 给发行物挂构建来源证明（in-toto / SLSA v1）
#
# 用法: ci/gates/attest.sh build   --out DIR --tag TAG [--kind iso|rel]
#                                  [--iso-rel REL] [--manifest FILE]
#                                  [--build-run RUN]
#       ci/gates/attest.sh publish --out DIR --tag TAG [--kind iso|rel]
#                                  [--iso-rel REL] [--ref REPO] [--token TOKEN]
#                                  [--force]
#       ci/gates/attest.sh verify  --file FILE (--iso FILE | --dir DIR)
#                                  [--build-id ID]
#
# 发行物带着 BUILD_ID 交付，只能回答「我是哪次构建」，回答不了「这次构建从
# 哪个提交、由哪条流水线、产出的到底是不是这一份」。前者是运行期自述，可以
# 伪造；后者必须由构建方在产物之外单独成证。本 gate 生成并发布这份证明，让
# 「装机后反查来源」从一句承诺变成一条可复算的记录（CI-17）。
#
# 两类发行物共用一条路径，只是 subject 的来源不同：
#   iso  subject 是装配目录里那唯一一盘 ISO，出证前与发行账本 <iso>.sha256
#        对账；账本与现算摘要不符说明 ISO 在写账本之后被改过，拒绝出证。
#   rel  subject 是发布页上的整套资产。集合不另立：直接取
#        ci/lib/release-assets.sh 的 airy_assets_want，与剪枝、对账同一份
#        事实源——三处各算一遍集会得到三种「本次发布了什么」。
#
# 证明的取值口径：
#   * buildId 取自镜像自带的构建清单（image-ci-manifest.txt），不是重新推导。
#     重新推导会得到「本 gate 认为应该是的值」，与镜像内实际写入的值一旦
#     分叉，证明就会替错误的镜像背书。清单缺失时才退回 buildid.sh 复算。
#     发布重放时作业本轮不是构建轮，--build-run 把指纹与 provenance 一并钉到
#     产出资产的那一轮；否则同一批资产会因为「谁在发布」而得到两份证明。
#   * 时间戳与 attempt 一律不写：同一构建轮加同一批资产必须产出同一份字节，
#     重放才不需要 --force。此前把发布轮写进 runId/attempt，真重放时字节
#     必然漂移，幂等判断无从成立。
#   * subject 的摘要现算，比较按集合：名字集合相等且逐项摘要相等才算过。
#     跳过某一项与改掉某一项是同一类错误，不能只查「清单里的都对得上」。
#
# 发布是内容寻址的：证明以 attest-<tag> 这个固定标签推入发行物所在仓库，
# 因此 oras manifest fetch <repo>:attest-<tag> 就能看到它，不需要先知道
# 产物摘要。推后回读比对注解、摘要与字节，任一处不符即失败，不留半份证明。
#
# 已发布证明与本地字节不同时，先比所证事实（buildId、commit、ref、kind、
# tag、subject 集合）：事实相同即同一份证明的另一种写法，保留原证明并跳过，
# 重放轮不必为空白差异重写历史；事实不同才拒绝，需 --force 才替换。
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
# 发布资产的期望集只在这一处定义：剪枝、对账与出证读同一份实现。
# shellcheck source=../lib/release-assets.sh
. "${LIB}/release-assets.sh"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly ROOT
readonly BUILDID="${ROOT}/distro/scripts/lib/buildid.sh"
# shellcheck source=../../distro/scripts/lib/buildid.sh
. "${BUILDID}"

CMD=""
KIND="iso"
OUT=""
TAG=""
ISO_REL="${ISO_REL_DEFAULT}"
MANIFEST=""
REF="${REF_DEFAULT}"
TOKEN=""
FORCE=0
FILE=""
ISO=""
SRC_DIR=""
BID=""
BUILD_RUN=""

usage()
{
    echo "用法: ci/gates/attest.sh build   --out DIR --tag TAG [--kind iso|rel] [--iso-rel REL] [--manifest FILE] [--build-run RUN]"
    echo "      ci/gates/attest.sh publish --out DIR --tag TAG [--kind iso|rel] [--iso-rel REL] [--ref REPO] [--token TOKEN] [--force]"
    echo "      ci/gates/attest.sh verify  --file FILE (--iso FILE | --dir DIR) [--build-id ID]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        build|publish|verify) CMD="$1"; shift ;;
        --kind)     KIND="${2:-}"; shift 2 ;;
        --out)      OUT="${2:-}"; shift 2 ;;
        --tag)      TAG="${2:-}"; shift 2 ;;
        --iso-rel)  ISO_REL="${2:-}"; shift 2 ;;
        --manifest) MANIFEST="${2:-}"; shift 2 ;;
        --ref)      REF="${2:-}"; shift 2 ;;
        --token)    TOKEN="${2:-}"; shift 2 ;;
        --force)    FORCE=1; shift ;;
        --file)     FILE="${2:-}"; shift 2 ;;
        --iso)      ISO="${2:-}"; shift 2 ;;
        --dir)      SRC_DIR="${2:-}"; shift 2 ;;
        --build-id) BID="${2:-}"; shift 2 ;;
        --build-run) BUILD_RUN="${2:-}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${CMD}" ] || { usage >&2; exit "${EINVAL}"; }
case "${KIND}" in
iso|rel) ;;
*) err "--kind 只接受 iso 或 rel: ${KIND}"; exit "${EINVAL}" ;;
esac
case "${BUILD_RUN}" in
"") ;;
*[!0-9]*) err "--build-run 只接受数字 run id: ${BUILD_RUN}"; exit "${EINVAL}" ;;
esac

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

att_out()
{
    if [ "${KIND}" = "iso" ]; then
        printf '%s/%s/attest-%s.json\n' "${OUT}" "${ISO_REL}" "${TAG}"
    else
        printf '%s/attest-%s.json\n' "${OUT}" "${TAG}"
    fi
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

# 发布作业没有检出 distro 子模块，airy_build_id 会退到第 4 级「配方摘要」，
# 而那里一个 spec 都读不到，结果是一个与产物无关的空摘要。run id 与检出
# 提交都是手上已有的原生量，补进环境让指纹停在正确的优先级上。
# --build-run 给出的是产出资产的那一轮，重放时它不等于作业本轮；指纹与
# provenance 都必须跟它走，否则同一批资产会随发布轮不同而得到不同证明。
att_run()
{
    if [ -n "${BUILD_RUN}" ]; then
        export AIRYMAX_RUN_ID="${BUILD_RUN}"
        export AIRYMAX_BUILD_RUN="${BUILD_RUN}"
    elif [ -z "${AIRYMAX_RUN_ID:-}" ] && [ -n "${GITHUB_RUN_ID:-}" ]; then
        export AIRYMAX_RUN_ID="${GITHUB_RUN_ID}"
    fi
    AIRYMAX_COMMIT="${AIRYMAX_COMMIT:-$(git -C "${ROOT}" rev-parse HEAD)}"
    export AIRYMAX_COMMIT
}

# subject 一律落成「名字<TAB>摘要」两列，生成与校验都只认这一种中间形态。
# 有了它，两类发行物的差别只剩「名字从哪来」，校验器不必知道种类。
att_set_iso()
{
    [ -f "$1" ] || die "ISO 不存在: $1"
    printf '%s\t%s\n' "${1##*/}" "$(sha256sum "$1" | cut -d' ' -f1)" >"$2"
}

# 发布资产按发布页上的名字登记，而工作树里 RPM 按架构分目录存放、发布页是
# 平铺的一层：只在 ${dir} 顶层找，会把「布局不同」误判成「资产缺失」。按名字
# 在树内定位，证明的 subject 才与页面对得上。同名多份时无法确定证的是哪一份，
# 必须失败而不是取第一个。
att_rel_file()
{
    local dir="$1" name="$2" hits n
    hits="$(find "${dir}" \( -name .git -prune \) -o -type f -name "${name}" -print)"
    n="$(printf '%s\n' "${hits}" | awk 'NF { n++ } END { print n + 0 }')"
    [ "${n}" -eq 1 ] || return 1
    printf '%s\n' "${hits}"
}

att_set_rel()
{
    local dir="$1" out="$2" f p
    [ -d "${dir}" ] || die "发布目录不存在: ${dir}"
    airy_assets_want "${dir}" "${WORK}/assets" || die "无法从摘要清单推导发布资产集"
    : >"${out}"
    while IFS= read -r f; do
        p="$(att_rel_file "${dir}" "${f}")" || die "发布资产缺失或重名: ${f}"
        printf '%s\t%s\n' "${f}" "$(sha256sum "${p}" | cut -d' ' -f1)" >>"${out}"
    done <"${WORK}/assets"
}

# 账本与现算摘要对账。缺账本同样是失败：没有账本就无从判断这份 ISO 是否在
# 出证之前被换过，而「无从判断」不能当成「没问题」。
att_ledger()
{
    local ledger="$1" subj="$2" want got
    [ -f "${ledger}" ] || die "缺少发行账本: ${ledger}"
    want="$(cut -f2 "${subj}")"
    got="$(awk 'NF >= 1 { print $1; exit }' "${ledger}")"
    [ "${want}" = "${got}" ] || die "发行账本摘要与证明不符: ${ledger}"
}

att_gen()
{
    local subj="${WORK}/subjects" file commit bid src
    [ -n "${OUT}" ] && [ -n "${TAG}" ] || { usage >&2; exit "${EINVAL}"; }
    need_cmd python3 sha256sum git
    att_run
    bid="$(att_bid)"
    commit="$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null)"
    [ -n "${commit}" ] || die "无法确定源码提交，拒绝出证"
    if [ "${KIND}" = "iso" ]; then
        src="$(att_dir)"
        att_set_iso "${src}" "${subj}"
        att_ledger "${src}.sha256" "${subj}"
    else
        att_set_rel "${OUT}" "${subj}"
    fi
    file="$(att_out)"
    python3 - "${file}" "${subj}" "${TAG}" "${KIND}" "${ISO_REL}" "${commit}" "${bid}" <<'PY' ||
import json
import os
import sys

out, subj_file, tag, kind, rel, commit, bid = sys.argv[1:8]
subjects = []
with open(subj_file, encoding="utf-8") as fh:
    for line in fh:
        name, digest = line.rstrip("\n").split("\t")
        subjects.append({"name": name, "digest": {"sha256": digest}})
repo = os.environ.get("GITHUB_REPOSITORY") or "openairymax/agent-linux"
server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
run = os.environ.get("AIRYMAX_BUILD_RUN") or os.environ.get("GITHUB_RUN_ID") or ""
ref = os.environ.get("GITHUB_REF") or ("refs/tags/" + tag)
builder = (server + "/" + repo + "/actions/runs/" + run) if run \
    else "local://ci/gates/attest.sh"
inv = (repo + "/actions/runs/" + run) if run else "local"
build_type = {
    "iso": "https://openairymax.org/buildtypes/oci-iso/v1",
    "rel": "https://openairymax.org/buildtypes/release-assets/v1",
}[kind]
params = {"tag": tag, "kind": kind, "subjectCount": len(subjects)}
if kind == "iso":
    params["arch"] = "x86_64"
    params["mediaRel"] = rel + "/" + subjects[0]["name"]
st = {
    "_type": "https://in-toto.io/Statement/v1",
    "subject": subjects,
    "predicateType": "https://slsa.dev/provenance/v1",
    "predicate": {
        "buildDefinition": {
            "buildType": build_type,
            "externalParameters": params,
            "internalParameters": {"buildId": bid, "commit": commit,
                                   "repository": repo, "runId": run,
                                   "ref": ref},
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
    att_check "${file}" "${subj}" "${bid}" ||
        die "生成后自检失败: ${file}"
    say "证明已生成: ${file}"
}

# 校验器同时服务三处：生成后自检、发布前复核、装机侧反查。任何一处走不同
# 实现都会让「证明有效」出现两种口径，所以只有这一个 python 块。
att_check()
{
    python3 - "$1" "${2:-}" "${3:-}" "${KIND}" <<'PY'
import json
import re
import sys

path, subj_file, bid, kind = sys.argv[1:5]
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
pred = st.get("predicate") or {}
params = (pred.get("buildDefinition") or {}).get("externalParameters") or {}
kind = params.get("kind") or kind
label = "ISO" if kind == "iso" else "资产"
have = {}
for item in st.get("subject") or []:
    name = item.get("name") or ""
    digest = ((item.get("digest") or {}).get("sha256") or "")
    if not name:
        errs.append("subject 缺文件名字")
    elif not re.fullmatch(r"[0-9a-f]{64}", digest):
        errs.append("%s 缺合法 sha256: %s" % (label, name))
    elif name in have:
        errs.append("%s 重复出现: %s" % (label, name))
    else:
        have[name] = digest
want = {}
if subj_file:
    with open(subj_file, encoding="utf-8") as fh:
        for line in fh:
            name, digest = line.rstrip("\n").split("\t")
            want[name] = digest
    if set(have) != set(want):
        missing = sorted(set(want) - set(have))[:3]
        extra = sorted(set(have) - set(want))[:3]
        errs.append("%s 集合与证明不符（缺 %s，多 %s）"
                    % (label, missing or "-", extra or "-"))
    for name in sorted(set(have) & set(want)):
        if have[name] != want[name]:
            errs.append("%s 摘要与证明不符: %s" % (label, name))
if not have:
    errs.append("%s 集合为空" % label)
ib = (pred.get("buildDefinition") or {}).get("internalParameters") or {}
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
names = ",".join(sorted(have)[:3])
print("AIRYMAX-ATTEST-PASS buildId=%s subject=%s name=%s"
      % (got, ",".join(have.values())[:64] if len(have) == 1
         else "%d 项" % len(have), names))
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
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    print((json.load(fh).get("annotations") or {}).get(sys.argv[2], ""))
PY
}

# 两份证明是不是在说同一件事：所证事实（谁构建的、哪个提交、哪一批摘要）
# 相同即等价，run 之外的行文差异（键顺序、空白、provenance 里的发布轮）
# 不构成新证明。事实不同则必须由人确认，不能靠 --force 覆盖掉旧记录。
att_facts()
{
    python3 - "$1" "$2" <<'PY'
import json
import sys


def facts(path):
    with open(path, encoding="utf-8") as fh:
        st = json.load(fh)
    bd = (st.get("predicate") or {}).get("buildDefinition") or {}
    ib = bd.get("internalParameters") or {}
    ext = bd.get("externalParameters") or {}
    subj = sorted((s.get("name"), (s.get("digest") or {}).get("sha256"))
                  for s in st.get("subject") or [])
    return {"buildId": ib.get("buildId"), "commit": ib.get("commit"),
            "ref": ib.get("ref"), "kind": ext.get("kind"),
            "tag": ext.get("tag"), "subjects": subj}


try:
    mine, theirs = facts(sys.argv[1]), facts(sys.argv[2])
except Exception as exc:  # 取回的不是证明 JSON，等同于「事实不可比」
    print("已发布证明不可解析: %s" % exc, file=sys.stderr)
    sys.exit(1)
diff = [k for k in mine if mine[k] != theirs[k]]
if diff:
    for k in diff:
        print("%s: 本地=%r 已发布=%r" % (k, mine[k], theirs[k]), file=sys.stderr)
    sys.exit(1)
PY
}

att_pub()
{
    local file sum bid n one set target have prev
    [ -n "${OUT}" ] && [ -n "${TAG}" ] || { usage >&2; exit "${EINVAL}"; }
    need_cmd oras python3 sha256sum
    file="$(att_out)"
    [ -s "${file}" ] || die "缺少证明，先运行 build: ${file}"
    sum="$(sha256sum "${file}" | cut -d' ' -f1)"
    # 注解里的 subject 口径随 subject 数量变：单件写它的摘要，多件写整份
    # 名字-摘要集合的摘要，并另记条数。回收端与反查端要的是「证明对上了
    # 哪一份东西」，条数让「集合被悄悄缩小」也能在注解层发现。
    { read -r n; read -r one; read -r set; read -r bid; } < <(python3 - "${file}" <<'PY'
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    st = json.load(fh)
sub = [(s["name"], s["digest"]["sha256"]) for s in st["subject"]]
set_digest = hashlib.sha256(
    "".join("%s %s\n" % (n, d) for n, d in sorted(sub)).encode()).hexdigest()
one = sub[0][1] if len(sub) == 1 else ""
print(len(sub))
print(one)
print(set_digest)
print(st["predicate"]["buildDefinition"]["internalParameters"]["buildId"])
PY
    )
    [ -n "${bid}" ] || die "证明缺 buildId: ${file}"
    att_login
    target="${REF}:attest-${TAG}"
    if att_fetch "${target}"; then
        have="$(att_ann "${ANN}.sha256")"
        if [ "${have}" = "${sum}" ]; then
            say "同一份证明已发布，跳过: ${target}"
            return 0
        fi
        mkdir -p "${WORK}/prev"
        prev="${WORK}/prev/${file##*/}"
        if oras pull "${target}" -o "${WORK}/prev" >/dev/null 2>&1 &&
            [ -s "${prev}" ]; then
            if att_facts "${file}" "${prev}" >"${WORK}/facts.log" 2>&1; then
                say "已发布证明所证事实与本地一致，保留原证明: ${target}"
                return 0
            fi
            awk '{ print "  差异: " $0 }' "${WORK}/facts.log" >&2
        fi
        [ "${FORCE}" -eq 1 ] ||
            die "已存在不同证明（sha256=${have:-空}）；确认替换才加 --force"
    fi
    ( cd "${file%/*}" && oras push --artifact-type "${ATTEST_TYPE}" \
        --annotation "${ANN}.sha256=${sum}" \
        --annotation "${ANN}.build-id=${bid}" \
        --annotation "${ANN}.subject-sha256=${one:-${set}}" \
        --annotation "${ANN}.subjects=${n}" \
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

# 反查必须指向一份可比对的实物：只有证明没有实物，验的就是「这份 JSON 自己
# 说自己是有效的」。
att_ver()
{
    local subj="${WORK}/subjects"
    [ -n "${FILE}" ] || { usage >&2; exit "${EINVAL}"; }
    need_cmd python3 sha256sum
    [ -f "${FILE}" ] || die "证明不存在: ${FILE}"
    if [ -n "${ISO}" ]; then
        att_set_iso "${ISO}" "${subj}"
    elif [ -n "${SRC_DIR}" ]; then
        att_set_rel "${SRC_DIR}" "${subj}"
    else
        usage >&2
        exit "${EINVAL}"
    fi
    att_check "${FILE}" "${subj}" "${BID}" ||
        die "证明校验失败: ${FILE}"
}

case "${CMD}" in
    build)   att_gen ;;
    publish) att_pub ;;
    verify)  att_ver ;;
esac
