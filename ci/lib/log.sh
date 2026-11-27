#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/log.sh — 统一日志、注解、断言与前置校验原语
#
# 目的：让同一条门禁在本地终端与 GitHub Actions 里输出同形。CI 专属的
# ::group:: 与 ::error:: 语法在本地退化为普通文本，不改变语义，也不引入
# 两套实现——这是「本地能跑 = CI 能跑」的前提。
#
# 判负型门禁的失败计数与内容断言（bad/have/deny）也收在这里：七个门禁曾各
# 写一行逐字相同的 bad()，而每个内容断言又各写一遍 `grep ... || bad`。断言
# 只有一种语义，落点也就只能有一处。
#
# 调用方：ci/ci.sh 与 ci/gates/*.sh。
# ===========================================================================

# 命名空间默认取调用脚本的文件名（去掉 .sh）。此前每个 gate 各自写一行
# CI_NS=<自己>，26 份赋值既重复又容易漏写；而且 shellcheck 逐个文件分析，
# 看不到本文件里的使用，26 处一律报 SC2034。改由这里派生后，新增门禁不必
# 再声明，日志前缀与实际脚本永远一致。
: "${CI_NS:=$(basename "${BASH_SOURCE[1]:-ci}" .sh)}"

say()
{
    printf '%s: %s\n' "${CI_NS}" "$*"
}

note()
{
    printf 'note: %s\n' "$*" >&2
}

group()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::group::%s\n' "$*"
    else
        printf '=== %s ===\n' "$*"
    fi
}

endgroup()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::endgroup::\n'
    fi
    return 0
}

warn()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::warning::%s\n' "$*" >&2
    else
        printf 'warning: %s\n' "$*" >&2
    fi
}

err()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::error::%s\n' "$*" >&2
    else
        printf 'error: %s\n' "$*" >&2
    fi
}

die()
{
    err "$*"
    exit 1
}

# FAILS / bad MSG —— 判负型门禁的失败计数。聚合而不遇错即退，是为了让
# 一次运行报出全部违约项；调用方在末尾判 FAILS 非零再退 1。
FAILS=0

bad()
{
    err "$*"
    FAILS=$((FAILS + 1))
}

# have PATTERN FILE MSG —— 文件内容必须命中断言；未命中记一次失败。
# 模式按 ERE 解释，调用方对 `$`、`(` 等元字符自行转义。
have()
{
    grep -qE "$1" "$2" || bad "$3"
}

# deny PATTERN FILE MSG —— 文件内容不得命中断言；命中记一次失败。
# 返回码恒为 0：断言结论走 FAILS，函数自身不该冒充门禁的判定结果。
deny()
{
    grep -qE "$1" "$2" && bad "$3"
    return 0
}

# need_cmd CMD... —— 前置工具校验，缺一个就立即失败。
#
# 放在这里而不是 host.sh：每个 gate 都会 source 本文件，而 need_cmd 只读
# 不写宿主状态，属于「任何门禁都可用的守卫」。此前它在 host.sh，四个未
# source host.sh 的 gate 调用时只得到 "command not found"，校验静默失效
# （run 40478538815 的 media-fetch/assemble/release-rpms/container-load）。
need_cmd()
{
    local c missing=()
    for c in "$@"; do
        command -v "${c}" >/dev/null 2>&1 || missing+=("${c}")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        die "缺少命令: ${missing[*]}"
    fi
}

# 参数不满足时门禁统一的退出码。与各 gate 自己的 EINVAL 同值，集中在此
# 是因为 need_dir 在这里定义：判定与它的退出码不该分居两个文件。调用方
# 另用自己的 EINVAL 表示同一约定时值必须相等，否则同一份门禁在不同分支
# 退出不同的码，编排层无法把它与「判定失败」区分开。
readonly CI_EINVAL=5

# need_dir OPT DIR —— 目录类选项的前置校验，缺参或目录不存在即退 5。
#
# 7 个 gate 曾各自抄一份同样的三行：两行判据加一行函数头，规范化后与
# 参数解析循环拼成同一个重复窗口。判定只有一份，落点也就只能有一处。
need_dir()
{
    [ -n "$2" ] || { err "$1 需要目录路径"; exit "${CI_EINVAL}"; }
    [ -d "$2" ] || { err "$1 目录不存在: $2"; exit "${CI_EINVAL}"; }
}

# need_file PATH WHAT —— 断言门禁依赖的产物存在（校验器、政策文件），缺则退 5。
#
# 五个门禁曾各自抄一份「校验器不存在就退 EINVAL」的四行块，其中三份逐字相同；
# 复制出来的判据不会同步演化：有人改了措辞或改判退出码，另一份仍按旧约定走，
# 编排层于是看到同一类失败退出两种码。判定只有一份，落点也只能有一处。
need_file()
{
    [ -f "$1" ] || { err "缺少${2}: $1"; exit "${CI_EINVAL}"; }
}

# need_spdx FILE... —— 新源文件首行须带 SPDX 标识、前五行须带版权行。
#
# 每个产出源文件的门禁都抄过一遍这两条 sed 断言，规范化后与相邻的
# group/endgroup 拼成重复窗口。文件头规范只有一份，判定也就只能有一处。
need_spdx()
{
    local f
    for f in "$@"; do
        sed -n '1p' "${f}" | grep -q 'SPDX-License-Identifier:' ||
            bad "$(basename "${f}"): 首行缺少 SPDX"
        sed -n '1,5p' "${f}" | grep -q 'Copyright' ||
            bad "$(basename "${f}"): 缺少版权行"
    done
}

# py_verify SCRIPT CHECK SRC [ARG...] —— 调校验器的 verify 子命令，回传退出码。
#
# «仓库根缺省为本脚本上两级、校验器必须存在、python3 必须可用、以
# verify --root SRC 调用» 这四步在 base-eol／ukui-lock／lang-lock 三个门禁里
# 逐字相同。抄一份的代价不是多几行，而是某天有人只改一处：同一份判据于是
# 有了两种退出码，编排层看不出差别。
#
# SRC 由调用方显式传入（空串表示取缺省），不替调用方回填变量：跨函数改调用
# 方的变量既读不出依赖，也会让静态检查报「赋值后未使用」。
py_verify()
{
    local script="$1" check="$2" src="$3"
    shift 3
    [ -n "${src}" ] || src="$(cd "$(dirname "${script}")/../.." && pwd -P)"
    need_file "${src}/${check}" 校验器
    need_cmd python3
    python3 "${src}/${check}" verify --root "${src}" "$@"
}

# env_set KEY VALUE —— 写入 GitHub Actions 的环境文件；本地运行时只打印，
# 由调用方自行 export。统一入口避免每个 gate 重复判断 $GITHUB_ENV。
env_set()
{
    local key="$1" val="$2" file="${CI_ENV_FILE:-${GITHUB_ENV:-}}"

    if [ -n "${file}" ]; then
        printf '%s=%s\n' "${key}" "${val}" >>"${file}"
    else
        printf 'export %s=%s\n' "${key}" "${val}" >&2
    fi
}

# out_set KEY VALUE —— 写入 GitHub Actions 的 step 输出文件；本地运行时只打印。
# 输出名与写入格式只在这里定义，gate 不再各自拼 "$GITHUB_OUTPUT"。
out_set()
{
    local key="$1" val="$2" file="${GITHUB_OUTPUT:-}"

    if [ -n "${file}" ]; then
        printf '%s=%s\n' "${key}" "${val}" >>"${file}"
    else
        printf 'output %s=%s\n' "${key}" "${val}" >&2
    fi
}
