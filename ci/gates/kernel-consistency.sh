#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-consistency.sh — 生成物与真相源的零漂移校验
#
# 用法:
#   ci/gates/kernel-consistency.sh [--kind all|matrix|abi|codegen|uapi]
#
# 前三条校验共用同一个性质：仓库里有「人工维护的真值」和「由真值生成的
# 产物」，一旦两者不一致，说明有人改了真值没重跑生成器，或改了生成物
# 没回头改真值。它们此前是 kernel-verify.yml 里三条一行的 run，分散在三处，
# 失败时还要翻 YAML 才知道调的是哪个脚本；收敛到一条门禁后，含义只看
# 这一个文件即可。
#
#   matrix   任务书矩阵（OS-STD-TOOL-161）
#   abi      ABI 布局黄金样本（OS-IRON-001）
#   codegen  代码生成零漂移
#
# 第四条 uapi 重建的是上游已删除的 headers_check 语义：导出的头文件
# 必须自包含编译。文本式 grep（用 __u32 就必须 include linux/types.h）
# 会误杀整个 [SC] 契约头体系——它们刻意经 uapi_compat.h 三方类型桥
# （非 Linux 平台 typedef stdint），因此这里以「逐头 standalone 编译」
# 为准，让编译器而非正则裁决自包含性。
#
# 退出码: 0 通过；1 有漂移；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
KIND=all

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-consistency.sh [--kind all|matrix|abi|codegen]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --kind) KIND="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

case "${KIND}" in all | matrix | abi | codegen | uapi) ;;
*)
    note "--kind 只接受 all|matrix|abi|codegen|uapi"
    exit "${EINVAL}"
    ;;
esac

cd "${REPO_ROOT}" || die "无法进入 ${REPO_ROOT}"

run_matrix()
{
    group "matrix: 任务书矩阵一致性（OS-STD-TOOL-161）"
    python3 tools/ci-matrix/ci_matrix.py --check
    local rc=$?
    endgroup
    return "${rc}"
}

run_abi()
{
    group "abi: ABI 布局黄金样本（OS-IRON-001）"
    python3 tools/abi-check/abi_check.py --check
    local rc=$?
    endgroup
    return "${rc}"
}

run_codegen()
{
    group "codegen: 生成物零漂移"
    python3 tools/codegen/run_codegen.py --check
    local rc=$?
    endgroup
    return "${rc}"
}

run_uapi()
{
    group "uapi: 导出头自包含编译（headers_check 语义）"
    local tmp out hdr rc=0
    tmp="$(mktemp -d)"
    # O= 是必须的：headers_install 会先生成 include/generated 与
    # arch/x86/include/generated，缺省落在源码树内——对 git status 不可见
    # （gitignored），却会被后续 KUnit 的 ARCH=um 构建当作陈旧 x86 头拾取，
    # 报 asm/types.h: No such file or directory。全部中间产物外置到临时目录。
    make -C kernel O="${tmp}/build" ARCH=x86_64 \
        headers_install INSTALL_HDR_PATH="${tmp}" >/dev/null 2>&1 ||
        { rm -rf "${tmp}"; die "headers_install 失败"; }
    out="${tmp}/check.c"
    for hdr in "${tmp}"/include/linux/airymax/*.h; do
        printf '#include <%s>\nint main(void){return 0;}\n' \
            "${hdr#"${tmp}"/include/}" >"${out}"
        # 不用 -nostdinc：真实消费场景是用户态带 libc 编译——stddef.h 来自
        # libc，linux/types.h 经 -I 解析到本安装树。剥掉 libc 反而是把
        # uapi_compat.h 刻意依赖的 offsetof 供给判成非自包含。
        if ! cc -I"${tmp}/include" -fsyntax-only "${out}" 2>"${tmp}/err"; then
            head -n 10 "${tmp}/err" >&2
            note "非自包含: ${hdr##*/}"
            rc=1
        fi
    done
    rm -rf "${tmp}"
    endgroup
    return "${rc}"
}

rc=0
case "${KIND}" in
all)
    run_matrix || rc=1
    run_abi || rc=1
    run_codegen || rc=1
    run_uapi || rc=1
    ;;
matrix) run_matrix || rc=1 ;;
abi) run_abi || rc=1 ;;
codegen) run_codegen || rc=1 ;;
uapi) run_uapi || rc=1 ;;
esac

if [ "${rc}" -ne 0 ]; then
    die "一致性校验未通过（kind=${KIND}）"
fi
say "一致性校验通过（kind=${KIND}）"
