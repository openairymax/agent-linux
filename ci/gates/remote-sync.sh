#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/remote-sync.sh — 多远程同 sha 的前置检查（CI-07）
#
# 派发里最贵的错误不是构建失败，而是把「本机有、镜像还没收」的提交编成
# 发行物：产物本身正确，校验和也齐全，只是没有任何一个远程能取到对应的
# 源码，于是这份发行物既不可复现也不可追溯。构建侧无法自证这件事——它只
# 看得见自己被 checkout 的那一份。
#
# 判据：本次要构建的 sha，必须在每个给定远程的同名分支上存在且相同。
# 落后、缺分支、不可达都判负；「不可达算过」会让门禁在断网时静默失效，
# 而那正是它要拦的场景。
#
# 传输方式不在这里拼：URL 由各 remote 自己配置决定，atomgit 侧按项目约定
# 只走 SSH，因此本门禁要求执行侧已具备相应凭据。开发机与发布机具备全部
# 远程；托管流水线只配置了 origin，于是它只校验自己那一份。
#
# 用法: ci/gates/remote-sync.sh [--repo DIR] [--ref REF] [--sha SHA]
#                               [--remote NAME]... [--timeout S]
# 退出码: 0 全部一致；1 存在落后/缺失/不可达；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

REPO=""
REF=""
KIND="heads"
SHA=""
TIMEOUT=60
NAMES=()

usage()
{
    cat <<'EOF'
用法: ci/gates/remote-sync.sh [选项]

  --repo DIR     仓库目录，缺省本仓库
  --ref REF      分支或标签名，缺省当前分支（分离头指针时必须显式给出）
  --kind K       heads 或 tags，缺省 heads；发版走 tags
  --sha SHA      期望 sha，缺省 --ref 指向的提交
  --remote NAME  参与的远程，可重复；缺省取仓库配置里的全部远程
  --timeout S    单次查询超时秒数，缺省 60
  -h, --help     显示本帮助

每个远程查一次 refs/<kind>/<ref>，与期望 sha 逐字比较；结果逐行打印，
任一行不是 ok 即整体判负。注解标签自动剥到提交再比。
EOF
}

parse_args()
{
    while [ "$#" -gt 0 ]; do
        case "$1" in
        --repo) REPO="${2:-}"; shift 2 ;;
        --ref) REF="${2:-}"; shift 2 ;;
        --kind) KIND="${2:-}"; shift 2 ;;
        --sha) SHA="${2:-}"; shift 2 ;;
        --remote) NAMES+=("${2:-}"); shift 2 ;;
        --timeout) TIMEOUT="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
        esac
    done
    REPO="${REPO:-${ROOT}}"
    # 子模块与 worktree 的 .git 是文件而非目录，只能问 git 自己。
    git -C "${REPO}" rev-parse --git-dir >/dev/null 2>&1 ||
        { err "不是 git 仓库: ${REPO}"; exit "${EINVAL}"; }
    case "${TIMEOUT}" in
    ''|*[!0-9]*) err "--timeout 必须为正整数: ${TIMEOUT}"; exit "${EINVAL}" ;;
    esac
    case "${KIND}" in
    heads|tags) ;;
    *) err "--kind 只能是 heads 或 tags: ${KIND}"; exit "${EINVAL}" ;;
    esac
}

# 缺省分支取当前分支。分离头指针下没有「当前分支」，此时凭空猜一个名字
# 只会查错对象，因此要求显式给出。
resolve_ref()
{
    if [ -n "${REF}" ]; then
        return 0
    fi
    REF="$(git -C "${REPO}" symbolic-ref --short -q HEAD)" ||
        { err "分离头指针，请显式给出 --ref"; exit "${EINVAL}"; }
}

resolve_sha()
{
    [ -n "${SHA}" ] && return 0
    if [ "${KIND}" = tags ]; then
        # 注解标签指向标签对象，构建用的是它剥出来的提交。
        SHA="$(git -C "${REPO}" rev-parse --verify -q \
            "refs/tags/${REF}^{commit}")" ||
            { err "本地没有标签 ${REF}"; exit "${EINVAL}"; }
        return 0
    fi
    SHA="$(git -C "${REPO}" rev-parse --verify -q "refs/heads/${REF}")" ||
        { err "本地没有分支 ${REF}"; exit "${EINVAL}"; }
}

default_remotes()
{
    if [ "${#NAMES[@]}" -ne 0 ]; then
        return 0
    fi
    while IFS= read -r name; do
        [ -n "${name}" ] && NAMES+=("${name}")
    done < <(git -C "${REPO}" remote)
    if [ "${#NAMES[@]}" -eq 0 ]; then
        err "仓库没有配置任何远程: ${REPO}"
        exit "${EINVAL}"
    fi
}

# 一次查询返回四行可能：拿得到、分支不在、远程不可达、超时。后三种都不能
# 当成「没问题」，但必须分开报，否则排障时看不出该修网络还是该补推送。
#
# stderr 不进捕获：命令替换只收 stdout，否则任何一行环境噪声都会挤进待解析
# 的文本里（本机 glibc 包装器对每个子进程都会吐一行告警，首例即被它顶掉）。
# 让 stderr 直接落到调用方的终端，git 自己的报错原样留在日志里。
probe()
{
    local name="$1" raw rc peeled
    raw="$(timeout "${TIMEOUT}" \
        git -C "${REPO}" ls-remote "${name}" "refs/${KIND}/${REF}" \
        "refs/${KIND}/${REF}^{}")"
    rc=$?
    if [ "${rc}" -eq 124 ]; then
        printf 'timeout\n'; return 0
    fi
    if [ "${rc}" -ne 0 ]; then
        printf 'unreachable\n'; return 0
    fi
    if [ -z "${raw}" ]; then
        printf 'missing\n'; return 0
    fi
    # 注解标签会同时给出标签对象与剥开后的提交，取后者；分支不会有 ^{} 行。
    peeled="$(printf '%s\n' "${raw}" | sed -n \
        's/^\([0-9a-f]\{40\}\)[[:space:]].*\^{}$/\1/p')"
    if [ -n "${peeled}" ]; then
        printf '%s\n' "${peeled}"
        return 0
    fi
    printf '%s\n' "${raw}" | sed -n '1s/[[:space:]].*//p'
}

main()
{
    parse_args "$@"
    resolve_ref
    resolve_sha
    default_remotes
    local name got bad=0
    printf '期望 %s = %s\n' "${REF}" "${SHA}"
    for name in "${NAMES[@]}"; do
        got="$(probe "${name}")"
        case "${got}" in
        timeout)      printf '%-10s %-48s %s\n' "${name}" "-" "超时"
                      bad=$((bad + 1)) ;;
        unreachable)  printf '%-10s %-48s %s\n' "${name}" "-" "不可达"
                      bad=$((bad + 1)) ;;
        missing)      printf '%-10s %-48s %s\n' "${name}" "-" "无此分支"
                      bad=$((bad + 1)) ;;
        "${SHA}")     printf '%-10s %-48s %s\n' "${name}" "${got}" "ok" ;;
        *)            printf '%-10s %-48s %s\n' "${name}" "${got}" "落后"
                      bad=$((bad + 1)) ;;
        esac
    done
    if [ "${bad}" -ne 0 ]; then
        err "${bad} 个远程与期望 sha 不一致，先双推再派发"
        exit 1
    fi
    say "全部远程同 sha"
}

main "$@"
