#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/payload-seed.sh — 给载荷发布端供料，以及构建侧按输入指纹取层
#
# 用法: ci/gates/payload-seed.sh probe --arch ARCH [--output FILE]
#                                     [--repomd-dir DIR]
#       ci/gates/payload-seed.sh fetch --dir DIR --arch ARCH
#                                     [--ref REPO] [--token TOKEN]
#       ci/gates/payload-seed.sh warm  --dir DIR --arch ARCH
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
# warm 是定时预热侧的发布入口，它不下载任何 RPM：字节来自上一轮构建留下的
# 闭包池（actions/cache，宿主与 registry 都在 GitHub 内网，跑托管 runner 也
# 不吃跨境限速），本 gate 只负责判断这份池是否对应当前上游。判断依据是构建
# 开始时 fetch 写下的 .input-key：池在那一轮被收割，标签就是那一段输入；定时
# 任务重算实时指纹，两者相等才推送。不相等说明上游在两次之间变过，此时推送
# 等于把旧字节挂在新的 key 上，取层的构建会整层丢弃重下，比不预热更慢。
#
# 允许「标签对得上但池不完整」的窗口（构建中途上游再变一次）是有意的：seed
# 层只是字节，消费端的求解仍以实时元数据或上一次成功事务的闭包清单为准逐包
# 校验 sha256，不符即丢。标签乐观只花一次带宽，不会把错包装进镜像。
#
# 退出码: 0 成功/无需预热/上游不可达；1 拉取、校验或推送失败；5 参数不满足。
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
CHECK=0

usage()
{
    echo "用法: ci/gates/payload-seed.sh probe --arch ARCH [--output FILE] [--repomd-dir DIR] [--check]"
    echo "      ci/gates/payload-seed.sh fetch --dir DIR --arch ARCH [--ref REPO] [--token TOKEN]"
    echo "      ci/gates/payload-seed.sh warm  --dir DIR --arch ARCH [--ref REPO] [--token TOKEN]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        probe|fetch|warm) CMD="$1"; shift ;;
        --dir)        DIR="${2:-}"; shift 2 ;;
        --arch)       ARCH="${2:-}"; shift 2 ;;
        --output)     OUTPUT="${2:-}"; shift 2 ;;
        --ref)        REF="${2:-}"; shift 2 ;;
        --token)      TOKEN="${2:-}"; shift 2 ;;
        --repomd-dir) REPOMD_DIR="${2:-}"; shift 2 ;;
        --check)      CHECK=1; shift ;;
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
        emit "missing=0"
        say "上游元数据不可达，本轮不预热"
        return 0
    fi
    ps_report
    emit "tag=${PS_TAG}"
    emit "key=${PS_KEY}"
    # 层已存在时后续预热一点字节都不该动：缓存整池恢复约 2.5 GB，而上游
    # 一天只变几批（§18.8.3「目标 tag 已存在 → 直接退出，零下载」）。判定
    # 不了（没有 oras、鉴权失败）一律当作缺失，让 warm 走完整判断——那边
    # 的推送端本来就会在 tag 已存在时跳过。
    if [ "${CHECK}" -eq 1 ] && ps_exist; then
        emit "missing=0"
        say "层已存在: ${PS_TAG}"
    else
        emit "missing=1"
    fi
}

ps_exist()
{
    command -v oras >/dev/null 2>&1 || return 1
    if [ -n "${TOKEN}" ]; then
        printf '%s\n' "${TOKEN}" |
            oras login "${REF%%/*}" --username airymax --password-stdin \
            >/dev/null 2>&1 || return 1
    fi
    oras manifest fetch "${REF}:${PS_TAG}" >/dev/null 2>&1
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
    ps_record
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

# 池的来源标签只在构建开始时写：那一池字节对应哪一段输入，只有收割它的
# 那一轮知道。定时任务拿它和实时指纹比对，决定能不能把池挂到当前 key 上。
ps_record()
{
    printf 'tag=%s\nkey=%s\narch=%s\nat=%s\n' \
        "${PS_TAG}" "${PS_KEY}" "${ARCH}" "$(date -u +%FT%TZ)" \
        >"${DIR}/.input-key" || die "无法写入输入标签: ${DIR}/.input-key"
}

# 清单只覆盖目录里已有的 RPM：预热不补差、不向上游取任何字节。条目按名字
# 排序，与 payload-publish 打包时的 --sort=name 一致，同一池两次预热得到
# 同一层摘要，重复推送才可能被判为「已有」而跳过。
ps_manifest()
{
    local f out="${tmp}/payload-manifest.txt" n
    n="$(find "${DIR}" -maxdepth 1 -type f -name '*.rpm' | wc -l | tr -d ' ')"
    [ "${n}" -gt 0 ] || return 1
    {
        printf '# AirymaxOS payload manifest\n'
        printf 'arch=%s\n' "${ARCH}"
        printf 'count=%s\n' "${n}"
        find "${DIR}" -maxdepth 1 -type f -name '*.rpm' | sort |
            while IFS= read -r f; do
                printf 'rpm %s %s\n' \
                    "$(sha256sum "${f}" | cut -d' ' -f1)" "${f##*/}"
            done
    } >"${out}"
}

warm()
{
    [ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }
    local src="${DIR}/.input-key" tag
    [ -f "${src}" ] || {
        say "池无输入标签（${src}），本轮不预热"
        return 0
    }
    if ! ps_key; then
        say "上游元数据不可达，本轮不预热"
        return 0
    fi
    tag="$(sed -n 's/^tag=//p' "${src}" | head -1)"
    if [ "${tag}" != "${PS_TAG}" ]; then
        say "池对应 ${tag:-未知}，实时指纹为 ${PS_TAG}，本轮不预热"
        return 0
    fi
    ps_manifest || {
        say "池内没有 RPM（${DIR}），本轮不预热"
        return 0
    }
    ps_report
    bash "${ROOT}/ci/gates/payload-publish.sh" push \
        --dir "${DIR}" --manifest "${tmp}/payload-manifest.txt" \
        --tag "${PS_TAG}" --arch "${ARCH}" \
        --ref "${REF}" --token "${TOKEN}" || {
        err "预热推送失败: ${PS_TAG}"
        return 1
    }
}

case "${CMD}" in
probe) probe ;;
fetch) fetch ;;
warm)  warm ;;
esac
