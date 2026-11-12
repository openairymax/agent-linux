#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/payload-publish.sh — 安装载荷的内容寻址发布与回取（L2）
#
# 用法: ci/gates/payload-publish.sh push --dir RPMDIR --manifest FILE --tag TAG
#                                        [--ref REPO] [--token TOKEN] [--no-verify]
#       ci/gates/payload-publish.sh pull --dir RPMDIR --tag TAG
#                                        [--ref REPO] [--token TOKEN]
#
# 载荷是本项目唯一进到用户机器字节的产物，而它此前只存在于某一轮 runner
# 的临时目录：ISO 编出来就随工件归档，载荷本身既不可复用也不可追溯。于是
# 每轮组装都要重新从公网解一遍安装事务（2.5 GB），跨轮缓存池又把「哪份池
# 是哪次事务的结果」隐式绑定在缓存键上，键一改就没有任何东西能回答「这些
# 字节从哪来」。本 gate 把载荷变成一次可发布的制品：内容寻址的 tag、随件
# 的清单、推送后的回读。
#
# tag 由调用方给出，两类语义不同，不可混用：
#   build-<ver>-<payload_key>   某轮真实组装产出的载荷，可反查产出它的清单；
#   seed-<arch>-<input_key>     按输入指纹预热的载荷，只提供字节。
# 本 gate 不推导 tag，只校验其形状：tag 一旦由这里猜，seed 与 build 的命名
# 就会漂移，回取端也就无法据 tag 判断「这份载荷是谁产出的」。
#
# 推送的前提是审计与计数都成立：清单里的 count= 必须等于实际 rpm 行数，
# 每一行的 sha256 必须与目录里的文件一致，任何一项不成立都拒绝推送。载荷
# 是安装期的全部输入，半份载荷一旦带上 tag，装机现场只会表现为「缺依赖」，
# 而错在数小时之前。因此「先审计再发布」，不是「发布后再校验」。
#
# tar 必须可复现：同一批文件在本机与云端要产出同一个摘要，否则 tag 的
# 内容寻址语义失效、回读比对也无从判定。排序、mtime 归零、属主归零，
# 三者缺一都会让摘要随构建机漂移。
#
# 推送后回读：tag 存在不代表字节能取回。回读把制品拉到临时目录，比对
# 文件数、逐包 sha256 与层摘要，全对才算发布成功；不一致说明这份 tag 已
# 不可信，删掉它并失败——留着它会让下一轮以「命中」的形态装进坏字节。
#
# 推送是幂等的：同 tag 已存在即跳过，不重推。重复推送既不改变字节，又会
# 在 registry 侧多留一份层，使镜像仓库的体积随预热次数线性增长。
#
# 每次推送写一条创建时间注解（org.openairymax.payload.created）。tag 里装的
# 是内容摘要，排序不出先后；保留策略要回答「哪几层最近推上去的」，只能自己
# 记一个时钟。注解随 manifest 走，幂等跳过时保持首次推送的时间，正好是回收
# 端需要的那一列。
#
# 回取端的失败分两类，与 payload_cache.sh 的判据一致：制品不存在是正常
# 状态（回落上游求解，慢但正确）；制品存在而字节不符是缓存污染，丢弃该
# 文件、保留其余，不升级为构建失败——池只提供字节，不提供事实。
#
# 退出码: 0 成功/已存在/制品未发布；1 审计或回读失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly REF_DEFAULT="ghcr.io/openairymax/airymaxos-payload"
readonly TYPE_ARTIFACT="application/vnd.airymaxos.payload.v1"
readonly TYPE_TAR="application/vnd.airymaxos.payload.tar"
readonly TYPE_MANIFEST="application/vnd.airymaxos.payload.manifest"
readonly ANN_CREATED="org.openairymax.payload.created"
readonly MARK_SRC=".payload-source"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

CMD=""
DIR=""
MANIFEST=""
TAG=""
REF="${REF_DEFAULT}"
TOKEN=""
ARCH=""
VERIFY=1

usage()
{
    echo "用法: ci/gates/payload-publish.sh push --dir RPMDIR --manifest FILE --tag TAG [--ref REPO] [--token TOKEN] [--no-verify]"
    echo "      ci/gates/payload-publish.sh pull --dir RPMDIR --tag TAG [--ref REPO] [--token TOKEN] [--arch ARCH]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        push|pull) CMD="$1"; shift ;;
        --dir)       DIR="${2:-}"; shift 2 ;;
        --manifest)  MANIFEST="${2:-}"; shift 2 ;;
        --tag)       TAG="${2:-}"; shift 2 ;;
        --ref)       REF="${2:-}"; shift 2 ;;
        --token)     TOKEN="${2:-}"; shift 2 ;;
        --arch)      ARCH="${2:-}"; shift 2 ;;
        --no-verify) VERIFY=0; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${CMD}" ] || { usage >&2; exit "${EINVAL}"; }
[ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }
[ -n "${TAG}" ] || { err "--tag 必填"; exit "${EINVAL}"; }
case "${TAG}" in
build-*|seed-*) ;;
*) err "--tag 必须以 build- 或 seed- 开头: ${TAG}"; exit "${EINVAL}" ;;
esac

need_cmd oras sha256sum tar find sort
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

login()
{
    local host="${REF%%/*}"
    [ -n "${TOKEN}" ] || return 0
    if ! printf '%s\n' "${TOKEN}" |
        oras login "${host}" --username airymax --password-stdin \
            >"${tmp}/login.log" 2>&1; then
        cat "${tmp}/login.log" >&2
        die "oras 登录失败: ${host}"
    fi
}

# 清单解析写进 PP_SUM/PP_BASE/PP_COUNT；只认 rpm 行，头注释与 comps 行跳过。
# count= 单独取出来，是为了让「清单自称几个包」与「实际列了几个包」可以对账。
PP_SUM=()
PP_BASE=()
PP_COUNT=""
PP_ARCH=""
PP_HIT=0
PP_N=0

pp_read()
{
    local a b c rest
    PP_SUM=()
    PP_BASE=()
    PP_COUNT=""
    PP_ARCH=""
    [ -r "$1" ] || return 1
    while read -r a b c rest; do
        case "${a}" in
        count=*) PP_COUNT="${a#count=}" ;;
        arch=*) PP_ARCH="${a#arch=}" ;;
        rpm) PP_SUM+=("${b}"); PP_BASE+=("${c}") ;;
        esac
    done <"$1"
    [ "${#PP_BASE[@]}" -gt 0 ]
}

pp_audit()
{
    local i base sum got bad=0
    pp_read "${MANIFEST}" || {
        err "载荷清单缺失或没有 rpm 行: ${MANIFEST}"
        return 1
    }
    [ -n "${PP_COUNT}" ] || {
        err "载荷清单缺少 count=，无法判定完整性"
        return 1
    }
    [ "${PP_COUNT}" = "${#PP_BASE[@]}" ] || {
        err "载荷清单计数不符: count=${PP_COUNT}，实际 ${#PP_BASE[@]} 行"
        return 1
    }
    if [ -n "${ARCH}" ] && [ -n "${PP_ARCH}" ] && [ "${PP_ARCH}" != "${ARCH}" ]; then
        err "载荷架构不符: 清单 ${PP_ARCH}，期望 ${ARCH}"
        return 1
    fi
    for i in "${!PP_BASE[@]}"; do
        base="${PP_BASE[$i]}"
        sum="${PP_SUM[$i]}"
        if [ ! -f "${DIR}/${base}" ]; then
            err "清单点名但目录内不存在: ${base}"
            bad=$((bad + 1))
            continue
        fi
        got="$(sha256sum "${DIR}/${base}" | cut -d' ' -f1)"
        [ "${got}" = "${sum}" ] && continue
        err "sha256 不符: ${base}（清单 ${sum}，实为 ${got}）"
        bad=$((bad + 1))
    done
    [ "${bad}" -eq 0 ]
}

push()
{
    local tar_out layer
    pp_audit || die "载荷未通过审计，拒绝发布（${MANIFEST}）"
    say "载荷审计通过：${#PP_BASE[@]} 个包（${DIR}）"

    login
    if oras manifest fetch "${REF}:${TAG}" >"${tmp}/exist.log" 2>&1; then
        say "tag 已存在，跳过推送: ${REF}:${TAG}"
        return 0
    fi
    if ! grep -qiE 'not found|404|manifest unknown|NAME_UNKNOWN' \
        "${tmp}/exist.log"; then
        cat "${tmp}/exist.log" >&2
        die "无法确认 tag 是否存在（鉴权或网络）: ${REF}:${TAG}"
    fi

    tar_out="${tmp}/payload.tar"
    if ! ( cd "${DIR}" &&
        tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
            -cf "${tar_out}" "${PP_BASE[@]}" ); then
        die "打包失败: ${DIR}"
    fi
    layer="$(sha256sum "${tar_out}" | cut -d' ' -f1)"
    say "载荷层 sha256: ${layer}"

    # 清单以固定名进制品：回取端只认 payload-manifest.txt，调用方传进来的
    # 路径名（可能是产物目录里的任意位置）不应决定制品内的布局。
    cp "${MANIFEST}" "${tmp}/payload-manifest.txt" ||
        die "无法复制清单: ${MANIFEST}"

    if ! oras push --artifact-type "${TYPE_ARTIFACT}" "${REF}:${TAG}" \
        --annotation "${ANN_CREATED}=$(date -u +%FT%TZ)" \
        "${tar_out}:${TYPE_TAR}" "${tmp}/payload-manifest.txt:${TYPE_MANIFEST}" \
        >"${tmp}/push.log" 2>&1; then
        cat "${tmp}/push.log" >&2
        die "oras 推送失败: ${REF}:${TAG}"
    fi

    [ "${VERIFY}" = "1" ] || {
        say "跳过回读（--no-verify）: ${REF}:${TAG}"
        return 0
    }
    pp_verify "${layer}"
}

# 回读判据按「字节是否等价」分层：层摘要相等即字节相等，这是内容寻址的
# 定义；文件数与逐包 sha256 是第二道，防的是清单与层不是同一次产出（推错
# 件、清单与 tar 来自不同轮次）。删 tag 用 best-effort：删失败也必须失败
# 退出，并把人工处置的命令打出来，不能留下一个「已知不可信」的 tag 让下
# 一轮当命中用。
pp_verify()
{
    local layer="$1" got n=0 i base
    if ! oras pull "${REF}:${TAG}" -o "${tmp}/back" >"${tmp}/back.log" 2>&1; then
        cat "${tmp}/back.log" >&2
        pp_revoke "回读拉取失败"
        return 1
    fi
    got="$(sha256sum "${tmp}/back/payload.tar" 2>/dev/null | cut -d' ' -f1)"
    if [ "${got}" != "${layer}" ]; then
        pp_revoke "回读层摘要不符（本地 ${layer}，远端 ${got:-缺失}）"
        return 1
    fi
    mkdir -p "${tmp}/back/x"
    tar -xf "${tmp}/back/payload.tar" -C "${tmp}/back/x" ||
        {
            pp_revoke "回读解包失败"
            return 1
        }
    n="$(find "${tmp}/back/x" -maxdepth 1 -type f -name '*.rpm' | wc -l |
        tr -d ' ')"
    if [ "${n}" != "${#PP_BASE[@]}" ]; then
        pp_revoke "回读文件数不符（本地 ${#PP_BASE[@]}，远端 ${n}）"
        return 1
    fi
    for i in "${!PP_BASE[@]}"; do
        base="${PP_BASE[$i]}"
        got="$(sha256sum "${tmp}/back/x/${base}" 2>/dev/null | cut -d' ' -f1)"
        if [ "${got}" != "${PP_SUM[$i]}" ]; then
            pp_revoke "回读 sha256 不符: ${base}"
            return 1
        fi
    done
    say "回读校验通过: ${REF}:${TAG}（${n} 个包，层 ${layer}）"
}

pp_revoke()
{
    local why="$1"
    err "${why}；该 tag 不可信，已尝试删除"
    if oras manifest delete --force "${REF}:${TAG}" >"${tmp}/rm.log" 2>&1; then
        err "已删除: ${REF}:${TAG}"
        return 0
    fi
    cat "${tmp}/rm.log" >&2
    err "删除失败，请人工执行: oras manifest delete --force ${REF}:${TAG}"
}

# pp_arch —— 架构门：清单声明的架构与请求不符时回落上游求解。请求未指定
# 架构、或清单缺该字段，都不构成拒绝理由。
pp_arch()
{
    [ -n "${ARCH}" ] || return 0
    [ -n "${PP_ARCH}" ] || return 0
    [ "${PP_ARCH}" != "${ARCH}" ] || return 0
    say "制品架构不符（清单 ${PP_ARCH}，期望 ${ARCH}），回落上游求解"
    return 1
}

# pp_get —— 拉取并解出载荷、读清单、过架构门；返回 1 表示回落上游求解
# （原因已打印），返回 0 表示 ${tmp}/in/x 可用。
pp_get()
{
    login
    if ! oras pull "${REF}:${TAG}" -o "${tmp}/in" >"${tmp}/in.log" 2>&1; then
        if grep -qiE 'not found|404|manifest unknown|NAME_UNKNOWN' \
            "${tmp}/in.log"; then
            say "制品尚未发布（${REF}:${TAG}），回落上游求解"
            return 1
        fi
        cat "${tmp}/in.log" >&2
        die "oras 拉取失败: ${REF}:${TAG}"
    fi
    [ -f "${tmp}/in/payload.tar" ] || die "制品缺少载荷层: ${REF}:${TAG}"
    mkdir -p "${tmp}/in/x"
    tar -xf "${tmp}/in/payload.tar" -C "${tmp}/in/x" ||
        die "载荷层解包失败: ${REF}:${TAG}"

    pp_read "${tmp}/in/payload-manifest.txt" || {
        say "制品清单不可用（${REF}:${TAG}），回落上游求解"
        return 1
    }
    pp_arch || return 1
    return 0
}

# pp_fill —— 逐包比对摘要后进池，计数写进 PP_HIT/PP_N。池只提供字节，
# 不提供事实：不符的文件丢弃，其余照常进池，本轮由 dnf 从上游补回这一份。
pp_fill()
{
    local base got i

    mkdir -p "${DIR}"
    for i in "${!PP_BASE[@]}"; do
        base="${PP_BASE[$i]}"
        PP_N=$((PP_N + 1))
        [ -f "${tmp}/in/x/${base}" ] || continue
        got="$(sha256sum "${tmp}/in/x/${base}" | cut -d' ' -f1)"
        if [ "${got}" != "${PP_SUM[$i]}" ]; then
            say "丢弃 ${base}（sha256 与清单不符）"
            continue
        fi
        [ -f "${DIR}/${base}" ] && {
            PP_HIT=$((PP_HIT + 1))
            continue
        }
        cp -p "${tmp}/in/x/${base}" "${DIR}/${base}" ||
            die "写入池失败: ${DIR}/${base}"
        PP_HIT=$((PP_HIT + 1))
    done
}

pull()
{
    local arch_note=""

    PP_HIT=0
    PP_N=0
    pp_get || return 0
    [ -n "${ARCH}" ] && arch_note=" arch=${PP_ARCH:-unknown}"
    pp_fill
    printf 'tag=%s\nrpm=%s\ntotal=%s\nsource=ghcr\n' \
        "${TAG}" "${PP_HIT}" "${PP_N}" >"${DIR}/${MARK_SRC}"
    say "回取 ${PP_HIT}/${PP_N}${arch_note}（${REF}:${TAG}）"
}

case "${CMD}" in
push)
    [ -n "${MANIFEST}" ] || { err "push 需要 --manifest"; exit "${EINVAL}"; }
    push
    ;;
pull) pull ;;
esac
