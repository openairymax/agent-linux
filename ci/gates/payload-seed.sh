#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/payload-seed.sh — 给载荷发布端供料，以及构建侧按输入指纹取层
#
# 用法: ci/gates/payload-seed.sh probe --arch ARCH [--output FILE]
#                                     [--repomd-dir DIR]
#       ci/gates/payload-seed.sh fetch --dir DIR --arch ARCH
#                                     [--ref REPO] [--token TOKEN]
#
# 发布端（payload-publish.sh）解决「载荷能不能按内容寻址取回」，本 gate 解决
# 「取哪一个」。区别不在字节，而在选择依据：`build-` 层的 tag 来自产出的载荷
# 本身，`seed-` 层的 tag 来自输入——上游五段仓库的元数据摘要、comps、锁定
# 输入清单、配方与镜像根表。上游没变，指纹就不变，构建侧因此可以在下载任何
# RPM 之前就知道「这一轮该取哪一层」。
#
# 只取 repomd.xml 是刻意的：五段合计不到 50 KB，而变化的所有可见征兆（包增删、
# 更新、撤回）都先体现在它上面。拉整份元数据或整站镜像都远超收益——后者是
# 数十 GB 且要承担镜像站的责任（§18.8.1）。
#
# 探针必须对实时上游取数，不复用任何缓存的元数据：缓存的 repomd 会把「上游
# 已经变了」这件事藏起来，而整条链的判据都建立在「指纹对应某一份真实快照」
# 之上（§18.8.4 约束 1）。
#
# 探针取不到数不是错误。托管 runner 到国内镜像的链路会整段停滞，此时唯一
# 正确的行为是「这一轮不预热」，而不是让构建或定时任务失败：预热只负责让
# 层提前存在，任何一方都不得先跑完不可（§18.8.3）。
#
# fetch 是构建侧的取层入口：算出 tag、取回、校验，全部通过才写完成标记。
# 标记与池同目录、随缓存一起保存，L1-b 的补差链据此判断「已有整份」而不是
# 「目录非空」——半份池同样非空（run 41075519752）。
#
# 退出码: 0 成功/无需预热/上游不可达；1 拉取或校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly REF_DEFAULT="ghcr.io/openairymax/airymaxos-payload"
readonly MARK=".seed-complete"
readonly PROBE_TMO="${PAYLOAD_SEED_TMO:-20}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly ROOT
readonly KS="${ROOT}/distro/kickstart/airymaxos-workstation.ks"
readonly SPEC="${ROOT}/distro/tools/ks_spec.py"
readonly COMPS="${ROOT}/distro/comps/airymaxos-comps.xml"
readonly RPMS_IN="${ROOT}/distro/lockfile/rpms.in.yaml"
readonly MIRRORS="${ROOT}/distro/repo/mirrors.list"

CMD=""
DIR=""
ARCH="x86_64"
OUTPUT=""
REF="${REF_DEFAULT}"
TOKEN=""
REPOMD_DIR=""

usage()
{
    echo "用法: ci/gates/payload-seed.sh probe --arch ARCH [--output FILE] [--repomd-dir DIR]"
    echo "      ci/gates/payload-seed.sh fetch --dir DIR --arch ARCH [--ref REPO] [--token TOKEN]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        probe|fetch) CMD="$1"; shift ;;
        --dir)        DIR="${2:-}"; shift 2 ;;
        --arch)       ARCH="${2:-}"; shift 2 ;;
        --output)     OUTPUT="${2:-}"; shift 2 ;;
        --ref)        REF="${2:-}"; shift 2 ;;
        --token)      TOKEN="${2:-}"; shift 2 ;;
        --repomd-dir) REPOMD_DIR="${2:-}"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${CMD}" ] || { usage >&2; exit "${EINVAL}"; }
case "${ARCH}" in
x86_64|aarch64|riscv64) ;;
*) err "未定义架构: ${ARCH}"; exit "${EINVAL}" ;;
esac

emit()
{
    [ -n "${OUTPUT}" ] || return 0
    printf '%s\n' "$1" >>"${OUTPUT}"
}

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# 输入指纹的组成部分写进同一份清单再取摘要：分层哈希（先算各段子摘要）让
# 「哪一段变了」在日志里可读，只留一个最终 key 时，上游一变就只能看到
# 「tag 变了」。
#
# 段序按配方声明（ks_spec repos 的输出顺序），不重排：镜像根的重排是探测
# 优化，与这里无关；这里要的是「同一份配方 + 同一份上游」→ 同一个指纹。
ps_parts()
{
    local name base url out sum bad=0
    ps_parts_file "comps $(sha256sum "${COMPS}" 2>/dev/null | cut -d' ' -f1)"
    ps_parts_file "rpms.in $(sha256sum "${RPMS_IN}" 2>/dev/null | cut -d' ' -f1)"
    ps_parts_file "ks $(sha256sum "${KS}" 2>/dev/null | cut -d' ' -f1)"
    ps_parts_file "mirrors $(sha256sum "${MIRRORS}" 2>/dev/null | cut -d' ' -f1)"
    while IFS=$'\t' read -r name base _; do
        [ -n "${name}" ] || continue
        url="${base//\$basearch/${ARCH}}"
        out="${REPOMD_DIR:-${tmp}}/${name}.xml"
        if [ -n "${REPOMD_DIR}" ]; then
            [ -f "${out}" ] || {
                err "缺少 ${name} 的 repomd 样本: ${out}"
                bad=$((bad + 1))
                continue
            }
        elif ! curl -fsSL --max-time "${PROBE_TMO}" --retry 2 \
            -o "${out}" "${url%/}/repodata/repomd.xml" 2>/dev/null; then
            printf 'payload seed: probe unavailable %s (%s)\n' "${name}" "${url}" >&2
            bad=$((bad + 1))
            continue
        fi
        # 只认真正的元数据。镜像站在路径缺失或改版时返回 200 的错误页，若不
        # 在此拦住，五段会算出同一个摘要，指纹从此恒等于「错误页的摘要」——
        # 看起来稳定，实际不再随上游变化（本机实测：未跟随 302 时五段同值）。
        if ! grep -q '<repomd' "${out}"; then
            printf 'payload seed: probe not metadata %s (%s)\n' "${name}" "${url}" >&2
            bad=$((bad + 1))
            continue
        fi
        sum="$(sha256sum "${out}" | cut -d' ' -f1)"
        ps_parts_file "repo:${name} ${sum}"
    done < <(python3 "${SPEC}" repos --skip-file "${KS}")
    [ "${bad}" -eq 0 ]
}

ps_parts_file()
{
    printf '%s\n' "$1" >>"${tmp}/parts.txt"
}

ps_key()
{
    local tag
    : >"${tmp}/parts.txt"
    ps_parts || return 1
    tag="$(sha256sum "${tmp}/parts.txt" | cut -d' ' -f1)"
    PS_TAG="seed-${ARCH}-$(printf '%s' "${tag}" | cut -c1-16)"
    PS_KEY="${tag}"
    return 0
}

probe()
{
    if ! ps_key; then
        emit "tag="
        say "上游元数据不可达，本轮不预热"
        return 0
    fi
    ps_report
    emit "tag=${PS_TAG}"
    emit "key=${PS_KEY}"
}

ps_report()
{
    say "输入指纹: ${PS_KEY}"
    say "目标 tag: ${PS_TAG}"
    sed 's/^/  /' "${tmp}/parts.txt" | head -12
}

fetch()
{
    [ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }
    mkdir -p "${DIR}" || die "无法创建池目录: ${DIR}"
    if [ -f "${DIR}/${MARK}" ]; then
        say "池已完整（${DIR}/${MARK}），跳过取层"
        return 0
    fi
    if ! ps_key; then
        say "上游元数据不可达，回落其它字节来源"
        return 0
    fi
    ps_report
    bash "${ROOT}/ci/gates/payload-publish.sh" pull \
        --dir "${DIR}" --tag "${PS_TAG}" --ref "${REF}" --token "${TOKEN}" || {
        err "取层失败: ${PS_TAG}"
        return 1
    }
    ps_mark
}

# 完成标记只在整层文件全部通过 sha256 之后写：回取端对不符的单包只丢弃、
# 不失败，因此「取到过」不等于「齐了」，判据必须是拿到的包数等于清单包数。
ps_mark()
{
    local src="${DIR}/.payload-source" rpm total
    [ -f "${src}" ] || {
        say "未命中发布层，不写完成标记"
        return 0
    }
    rpm="$(sed -n 's/^rpm=//p' "${src}" | head -1)"
    total="$(sed -n 's/^total=//p' "${src}" | head -1)"
    if [ -z "${rpm}" ] || [ "${rpm}" != "${total}" ]; then
        say "层不完整（${rpm:-0}/${total:-0}），未写完成标记"
        return 0
    fi
    printf 'tag=%s\nrpm=%s\n' "${PS_TAG}" "${rpm}" >"${DIR}/${MARK}" ||
        die "无法写入完成标记: ${DIR}/${MARK}"
    say "取层完成: ${rpm} 个包（${PS_TAG}）"
}

case "${CMD}" in
probe) probe ;;
fetch) fetch ;;
esac
