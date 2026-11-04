#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/mgmt-files.sh — 治理文件与子模块完整性
#
# 用法: ci/gates/mgmt-files.sh [--only 检查项]
#
# 六项断言此前是 mgmt-verify.yml 与 sc-verify.yml 里的内联 shell：子模块
# 目录、子模块检出状态、[SC] 12 头文件、[SC] ipc.h 唯一物理副本、治理文件
# 清单、废弃前缀。它们只读工作树、不触碰宿主，属于典型的「同一判据要在
# 本地先跑一遍」的逻辑，因此下沉到本 gate；YAML 只剩一次分派。
#
# --only 只跑其中一项：整树断言（子模块检出、[SC] 头文件、CODEOWNERS 投影）
# 没有完整仓库就无从成立，而单项判据本身仍需负向回归（见 ci/tests/
# test_mgmt_files.sh）。不给这个入口，被检项就只能在真树上正向跑一遍。
#
# CODEOWNERS 一致性也在此校验：CODEOWNERS 头部声称「CI validates that
# CODEOWNERS and MAINTAINERS stay in sync」，此前没有任何实现支撑，生成器
# 还把 49 行文件覆盖成 4 行。现在由 tools/repo/codeowners.py 投影，本 gate 只做
# 比对，漂移即失败。
#
# 子模块清单只有 .gitmodules 一个来源，不在此复制 9 个路径——复制就会在
# 新增子仓时静默漏检。
#
# 退出码: 0 全部通过；1 任一断言失败。
# ===========================================================================
set -euo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "${ROOT}"

readonly EINVAL=5

readonly -a SC_HEADERS=(
    error.h log_types.h ipc.h sched.h memory_types.h security_types.h
    cognition_types.h syscalls.h syscall_gen.h uapi_compat.h lsm_types.h
    bpf_struct_ops.h
)

readonly -a GOV_FILES=(
    README.md README_zh.md LICENSE NOTICE CONTRIBUTING.md MAINTAINERS
    SECURITY.md .editorconfig .clang-format CODEOWNERS .gitignore .gitmodules
)

sub_paths()
{
    git config -f .gitmodules --get-regexp '^submodule\..*\.path$' |
        awk '{ print $2 }'
}

sub_dirs()
{
    local -a subs
    local sub n=0

    mapfile -t subs < <(sub_paths)
    [ "${#subs[@]}" -gt 0 ] || die ".gitmodules 未声明任何子模块"
    for sub in "${subs[@]}"; do
        [ -d "${sub}" ] || die "子模块目录缺失: ${sub}/"
        n=$((n + 1))
    done
    say "子模块目录齐备: ${n} 个"
}

sub_state()
{
    local declared checked

    declared="$(sub_paths | sort)"
    checked="$(git submodule status | awk '{ print $2 }' | sort)"
    [ "${declared}" = "${checked}" ] || {
        diff <(printf '%s\n' "${declared}") <(printf '%s\n' "${checked}") || true
        die "git submodule status 与 .gitmodules 不一致"
    }
    say "子模块检出状态与 .gitmodules 一致"
}

sc_headers()
{
    local h

    for h in "${SC_HEADERS[@]}"; do
        [ -f "kernel/include/uapi/linux/airymax/${h}" ] ||
            die "[SC] 头文件缺失: ${h}"
    done
    say "[SC] 共享契约头文件齐备: ${#SC_HEADERS[@]} 个"
}

sc_unique()
{
    local -a hits
    local n

    # 判据是「被 git 跟踪的路径」：kernel 是完整 Linux 树，自带上百个同名
    # ipc.h（linux/、apparmor/、selftests/ …），必须限定到 airymax/；而
    # kernel/usr/include/ 是 headers_install 的构建产物，本地与 CI 都可能
    # 残留，用工作树扫描会把它误判成第二份源码副本。
    mapfile -t hits < <(git ls-files --recurse-submodules -- '*/airymax/ipc.h')
    n="${#hits[@]}"
    if [ "${n}" -ne 1 ]; then
        printf '  %s\n' "${hits[@]}" >&2
        die "[SC] ipc.h 物理副本数应为 1，实际 ${n}"
    fi
    say "[SC] ipc.h 唯一物理副本: ${hits[0]}"
}

gov_files()
{
    local f

    for f in "${GOV_FILES[@]}"; do
        [ -f "${f}" ] || die "治理文件缺失: ${f}"
    done
    say "治理文件齐备: ${#GOV_FILES[@]} 个"
}

# 废弃前缀判据约束的是**仓库命名**（2026-07-12 决策），不是禁止某个词串
# 出现在文档里。决策原文是「8 子仓裸名，禁止 airymaxos- 前缀」：它管的是新
# 仓库叫什么。此前用裸子串匹配，把 GHCR 的 OCI 包名 airymaxos-iso 与发行
# 产物名 airymaxos-<ver>-x86_64.iso 一并判红——这两个是合法交付物（ISO 走
# 独立包，见 ci/gates/iso-publish.sh），于是每次改 README 治理门禁必红，
# 长期噪声化之后没人再看它，真正的越界命名反而能被忽略。
no_prefix()
{
    local -a names
    local IFS='|' pat hits f

    # 缺文件不是「无前缀」：grep 的 rc=2（文件不存在）在新旧两版都走不到
    # die，空树会被读成合规。先把被检文件的存在性判掉。
    for f in README.md README_zh.md MAINTAINERS; do
        [ -f "${f}" ] || die "[NP] 缺少被检文件: ${f}"
    done
    mapfile -t names < <(sub_paths | while read -r p; do basename "${p}"; done |
        sort -u)
    [ "${#names[@]}" -gt 0 ] || die ".gitmodules 未声明任何子模块"
    # 禁止集合由 .gitmodules 的裸名派生：新增子仓自动纳入，不在此维护第二
    # 份名单；匹配到裸名边界即止（airymaxos-kernel-6.6 同样命中）。
    pat="airymaxos-(${names[*]})\\b"
    if hits="$(grep -rnE "${pat}" README.md README_zh.md MAINTAINERS 2>/dev/null)"; then
        printf '%s\n' "${hits}" >&2
        die "发现已废弃的 airymaxos- 前缀（2026-07-12 决策）"
    fi
    say "无废弃的 airymaxos- 前缀（裸名 ${#names[@]} 个）"
}

owners_sync()
{
    python3 tools/repo/codeowners.py --check ||
        die "CODEOWNERS 与 MAINTAINERS 不一致（make codeowners-sync）"
    say "CODEOWNERS 与 MAINTAINERS 同步"
}

usage()
{
    cat <<'EOF'
用法: ci/gates/mgmt-files.sh [--only 检查项]

检查项: sub-dirs sub-state sc-headers sc-unique gov-files owners-sync
        no-prefix
EOF
}

run_one()
{
    case "$1" in
        sub-dirs) sub_dirs ;;
        sub-state) sub_state ;;
        sc-headers) sc_headers ;;
        sc-unique) sc_unique ;;
        gov-files) gov_files ;;
        owners-sync) owners_sync ;;
        no-prefix) no_prefix ;;
        *)
            err "未知检查项: $1"
            usage >&2
            exit "${EINVAL}"
            ;;
    esac
}

main()
{
    group "治理文件与子模块完整性"
    if [ -n "${ONLY}" ]; then
        run_one "${ONLY}"
        say "单项检查通过: ${ONLY}"
        endgroup
        return 0
    fi
    sub_dirs
    sub_state
    sc_headers
    sc_unique
    gov_files
    owners_sync
    no_prefix
    endgroup
    say "治理门禁通过"
}

ONLY=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --only)
            [ -n "${2:-}" ] || { usage >&2; exit "${EINVAL}"; }
            ONLY="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage >&2; exit "${EINVAL}" ;;
    esac
done

main "$@"
