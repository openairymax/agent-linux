#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-analyze.sh — Airymax 自有代码的静态检查
#
# 用法:
#   ci/gates/kernel-analyze.sh --kind checkpatch|sparse|coccinelle
#                              [--src DIR] [--log FILE]
#
# 三种检查的共同前提是「只扫 Airymax 自有代码」，不是整棵树：整树
# checkpatch/sparse/coccicheck 在单 job 时限内跑不完，而且上游代码的
# 存量告警与本仓库无关，混进来只会淹掉真正的问题。
#
# 各自的红线口径:
#   checkpatch  --strict 下只要有 ERROR/WARNING/CHECK 就返回 1，所以门禁
#               不能看退出码，必须解析 total 行的 errors 计数；只卡
#               ERROR，WARNING/CHECK 仅报告（L-11 归一化）。
#   sparse      仅对 refbuild 产出的 .o 触发 C=2 W=2，命中 Airymax 路径
#               的 warning/error 即失败；没有目标文件直接失败，避免
#               「零对象零告警」的假绿。
#   coccinelle  10 个脚本 × 6 个目录；coccicheck 把 SPFLAGS 追加在最后，
#               用 --dir 把默认的整树扫描收窄到单个目录。
#
# 退出码: 0 通过；1 发现问题；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
KIND=""
LOG=""

# 参与检查的 Airymax 目录，三个 kind 共用同一份清单。
readonly AIRY_DIRS=(
    security/airy
    kernel/superv
    kernel/syscalls
    kernel/corekern
    kernel/log
    kernel/ipc
)

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-analyze.sh --kind checkpatch|sparse|coccinelle
                                 [--src DIR] [--log FILE]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --kind) KIND="${2:-}"; shift 2 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --log) LOG="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

case "${KIND}" in checkpatch | sparse | coccinelle) ;;
*)
    note "--kind 只接受 checkpatch|sparse|coccinelle"
    usage >&2
    exit "${EINVAL}"
    ;;
esac

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"

cd "${SRC}" || die "无法进入 ${SRC}"

run_checkpatch()
{
    local log="${LOG:-checkpatch.log}"
    local files=()
    local d
    for d in "${AIRY_DIRS[@]}" include/uapi/linux/airymax include/airymax; do
        [ -d "${d}" ] || continue
        while IFS= read -r f; do
            files+=("${f}")
        done < <(find "${d}" -type f \( -name '*.c' -o -name '*.h' \) |
            LC_ALL=C sort)
    done
    [ "${#files[@]}" -gt 0 ] || die "checkpatch 没有可检查的源文件"

    say "checkpatch 目标: ${#files[@]} 个文件"
    local err_total=0 warn_total=0 chk_total=0
    : >"${log}"
    local f out total errs warns chks
    for f in "${files[@]}"; do
        out="$(LC_ALL=C scripts/checkpatch.pl --strict --no-tree -f "${f}" 2>&1 || true)"
        total="$(printf '%s\n' "${out}" | grep -m1 '^total:' || true)"
        errs="$(printf '%s\n' "${total}" |
            sed -n 's/^total: \([0-9]\{1,\}\) errors.*/\1/p')"
        warns="$(printf '%s\n' "${total}" |
            sed -n 's/.*, \([0-9]\{1,\}\) warnings.*/\1/p')"
        chks="$(printf '%s\n' "${total}" |
            sed -n 's/.*, \([0-9]\{1,\}\) checks.*/\1/p')"
        err_total=$((err_total + ${errs:-0}))
        warn_total=$((warn_total + ${warns:-0}))
        chk_total=$((chk_total + ${chks:-0}))
        printf '%s\n' "${out}" >>"${log}"
        if [ "${errs:-0}" -gt 0 ]; then
            err "checkpatch: ${f} 有 ${errs} 个 ERROR"
            printf '%s\n' "${out}" | grep '^ERROR:' >&2 || true
        fi
    done
    say "checkpatch 合计: ${err_total} errors, ${warn_total} warnings, ${chk_total} checks"
    [ "${err_total}" -eq 0 ] || die "checkpatch 门禁失败（${err_total} errors）"
    say "checkpatch 通过（仅卡 ERROR，WARNING/CHECK 只报告）"
}

run_sparse()
{
    local log="${LOG:-sparse.log}"
    local objs=()
    local c
    for d in "${AIRY_DIRS[@]}"; do
        [ -d "${d}" ] || continue
        while IFS= read -r c; do
            [ -f "${c%.c}.o" ] && objs+=("${c%.c}.o")
        done < <(find "${d}" -name '*.c')
    done
    # 没有目标文件说明参考构建没跑或产物被清掉了。此时 C=2 会「零对象零
    # 告警」地通过，是最危险的假绿，必须显式失败。
    [ "${#objs[@]}" -gt 0 ] || die "找不到已编译的 Airymax 目标文件，先跑 kernel-refbuild.sh"

    say "sparse 目标: ${#objs[@]} 个目标文件"
    make ARCH=x86_64 C=2 W=2 -j"$(nproc)" "${objs[@]}" 2>&1 | tee "${log}"
    local pat='^(security/airy|kernel/(superv|syscalls|corekern|log|ipc))/[^:]+:[0-9]+:[0-9]+: (warning|error):'
    if grep -E "${pat}" "${log}"; then
        die "sparse 在 Airymax 代码上报告了问题"
    fi
    say "sparse 通过（仅 Airymax 目标）"
}

run_coccinelle()
{
    local log="${LOG:-cocci.log}"
    local scripts=(
        scripts/coccinelle/misc/doubleinit.cocci
        scripts/coccinelle/misc/ifcol.cocci
        scripts/coccinelle/misc/minmax.cocci
        scripts/coccinelle/misc/returnvar.cocci
        scripts/coccinelle/misc/semicolon.cocci
        scripts/coccinelle/misc/uninitialized_var.cocci
        scripts/coccinelle/free/ifnullfree.cocci
        scripts/coccinelle/locks/double_lock.cocci
        scripts/coccinelle/null/deref_null.cocci
        scripts/coccinelle/null/kmerr.cocci
    )
    local s d
    : >"${log}"
    for s in "${scripts[@]}"; do
        [ -f "${s}" ] || die "缺少 coccinelle 脚本: ${s}"
        for d in "${AIRY_DIRS[@]}"; do
            [ -d "${d}" ] || continue
            # coccicheck 把 SPFLAGS 追加在最后，spatch 冲突时取最后一个选项，
            # 由此把默认的 --dir $srctree 收窄到单个 Airymax 目录。
            make -s ARCH=x86_64 coccicheck MODE=report J=1 \
                COCCI="${s}" SPFLAGS="--dir ${d}" >>"${log}" 2>&1 || true
        done
    done
    grep -q '^coccicheck failed' "${log}" &&
        die "coccicheck 未跑完即中止（见 ${log}）"
    local pat='^(security/airy|kernel/(superv|syscalls|corekern|log|ipc))/[^:]+:[0-9]+:[0-9]+'
    if grep -E "${pat}" "${log}"; then
        die "Coccinelle 在 Airymax 代码上有发现"
    fi
    say "Coccinelle 通过（${#scripts[@]} 脚本 × Airymax 目录）"
}

case "${KIND}" in
checkpatch) run_checkpatch ;;
sparse) run_sparse ;;
coccinelle) run_coccinelle ;;
esac
