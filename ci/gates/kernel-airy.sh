#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-airy.sh — Airymax 自有内核代码的归属断言
#
# 用法:
#   ci/gates/kernel-airy.sh --kind assert --arch ARCH [--src DIR]
#   ci/gates/kernel-airy.sh --kind style [--src DIR]
#
# 只承担「没有别的门禁在查」的两件事，其余一律交给既有门禁，避免同一份
# 断言在两个地方各写一遍后悄悄漂移：
#
#   assert  —— 构建产物必须真的由 Airymax 代码构成。配置三步法与编译由
#              kernel-build.sh 完成，本门禁只断言其结果：10 个必备 CONFIG
#              精确匹配、CONFIG_LSM 含 airy、vmlinux 带 .BTF 段、六个
#              built-in.a 存在，x86_64 上再断言 security/airy 与
#              kernel/superv 的全局符号互不冲突。
#   style   —— Airymax 文件的 SPDX 归属。checkpatch、扩展规则、defconfig
#              矩阵、BTF 基线与 Kbuild 版本注入已分别由 kernel-verify.yml
#              与 os-chk-verify.yml（tools/os-chk/gates.yaml）承接。
#
# REQUIRED_CONF 同时是 tools/defconfig-matrix 的校验载具：清单与这里逐项
# 相等才算通过（OS-BUILD-026），因此不要在此增删项而不改 manifest.yaml。
#
# 退出码: 0 通过；1 断言失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
KIND=""
ARCH=""

# 写成完整 KV 而不是 K 前缀，是为了同时锁住开关的值（y 被改成 m 也算红）。
readonly REQUIRED_CONF=(
    'CONFIG_SECURITY=y'
    'CONFIG_SECURITY_AIRY=y'
    'CONFIG_AIRY_SYSCALL=y'
    'CONFIG_AIRY_COREKERN=y'
    'CONFIG_AIRY_LOG=y'
    'CONFIG_AIRY_IPC=y'
    'CONFIG_AIRY_AGENT_TRACING=y'
    'CONFIG_AIRY_CAP_TABLE_SIZE=1024'
    'CONFIG_IO_URING=y'
    'CONFIG_USER_NS=y'
)

readonly AIRY_DIRS=(
    security/airy
    kernel/superv
    kernel/syscalls
    kernel/corekern
    kernel/log
    kernel/ipc
)

readonly SYM_OBJS=(
    security/airy/airy_cap_check.o
    kernel/superv/airy_cap_check_superv.o
)

usage()
{
    cat <<'EOF'
用法:
  ci/gates/kernel-airy.sh --kind assert --arch ARCH [--src DIR]
  ci/gates/kernel-airy.sh --kind style [--src DIR]
EOF
}

arg_fail()
{
    note "$1"
    usage >&2
    exit "${EINVAL}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --kind) KIND="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    *) arg_fail "未知参数: $1" ;;
    esac
done

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"

check_conf()
{
    group "必备 CONFIG"
    local want
    for want in "${REQUIRED_CONF[@]}"; do
        grep -qx "${want}" .config || die "构建产物缺少 ${want}"
    done
    local lsm
    lsm="$(sed -n 's/^CONFIG_LSM="\(.*\)"$/\1/p' .config)"
    case ",${lsm}," in
    *,airy,*) say "CONFIG_LSM 含 airy: ${lsm}" ;;
    *) die "CONFIG_LSM 不含 airy: ${lsm}" ;;
    esac
    endgroup
}

check_btf()
{
    group "BTF 基线"
    # Linux 6.6 由 gen_btf() 把 BTF 内嵌进 .BTF 段，不产出独立文件
    # （OS-KER-106 / OS-OBS-012），因此断言段存在而不是断言文件存在。
    readelf -S vmlinux | grep -q '\.BTF' ||
        die "vmlinux 缺少 .BTF 段（CONFIG_DEBUG_INFO_BTF 未生效）"
    say "vmlinux 带 .BTF 段"
    endgroup
}

check_arts()
{
    group "构建产物"
    local dir
    for dir in "${AIRY_DIRS[@]}"; do
        [ -f "${dir}/built-in.a" ] || die "缺少 ${dir}/built-in.a"
    done
    say "${#AIRY_DIRS[@]} 个 built-in.a 齐备"
    endgroup
}

check_syms()
{
    local obj
    for obj in "${SYM_OBJS[@]}"; do
        [ -f "${obj}" ] || die "缺少 ${obj}"
    done
    # 重命名后的 *_superv 变体不得与宿主模块同名导出：两层用同一份工程
    # 规范，符号一旦重合，链接顺序就会决定谁生效。
    nm -g --defined-only "${SYM_OBJS[0]}" | awk '{print $3}' | LC_ALL=C sort -u >a.sym
    nm -g --defined-only "${SYM_OBJS[1]}" | awk '{print $3}' | LC_ALL=C sort -u >b.sym
    if comm -12 a.sym b.sym | grep -q .; then
        comm -12 a.sym b.sym >&2
        rm -f a.sym b.sym
        die "两个对象存在同名全局符号"
    fi
    rm -f a.sym b.sym
    say "符号无冲突"
}

run_assert()
{
    [ -n "${ARCH}" ] || arg_fail "assert 需要 --arch"
    cd "${SRC}" || die "无法进入 ${SRC}"
    [ -f .config ] || die "缺少 ${SRC}/.config（请先跑 kernel-build.sh）"
    check_conf
    check_btf
    check_arts
    group "符号完整性"
    if [ "${ARCH}" = x86_64 ]; then
        check_syms
    else
        note "${ARCH} 不做符号断言（对象名只在 x86_64 参考构建里固定）"
    fi
    endgroup
    say "内核归属断言通过: ${ARCH}"
}

check_spdx()
{
    local expected="$1"
    shift
    local f bad=0
    for f in "$@"; do
        head -1 "${f}" | grep -qF "SPDX-License-Identifier: ${expected}" || {
            note "${f} 须声明 'SPDX-License-Identifier: ${expected}'"
            bad=1
        }
    done
    [ "${bad}" -eq 0 ] || return 1
}

run_style()
{
    cd "${SRC}" || die "无法进入 ${SRC}"
    group "SPDX 归属"
    # 期望值依据 docs/docs-linux/50-engineering-standards/05-development-process.md
    # Part IV：UAPI 头 -> Linux-syscall-note；内核态 .c/.h -> GPL-2.0-only；
    # 构建脚本 Kbuild -> GPL-2.0。
    local -a uapi_h uapi_kbuild airy_c airy_h kbuild
    mapfile -t uapi_h < <(find include/uapi/linux/airymax -maxdepth 1 -name '*.h' | LC_ALL=C sort)
    mapfile -t airy_c < <(find "${AIRY_DIRS[@]}" -type f -name '*.c' | LC_ALL=C sort)
    mapfile -t airy_h < <(find "${AIRY_DIRS[@]}" include/airymax -type f -name '*.h' | LC_ALL=C sort)
    mapfile -t kbuild < <(find "${AIRY_DIRS[@]}" -type f -name 'Kbuild' | LC_ALL=C sort)
    uapi_kbuild=(include/uapi/linux/airymax/Kbuild)

    local rc=0
    check_spdx 'GPL-2.0-only WITH Linux-syscall-note' "${uapi_h[@]}" || rc=1
    check_spdx 'GPL-2.0-only' "${airy_c[@]}" || rc=1
    check_spdx 'GPL-2.0-only' "${airy_h[@]}" || rc=1
    check_spdx 'GPL-2.0-only' "${kbuild[@]}" || rc=1
    check_spdx 'GPL-2.0' "${uapi_kbuild[@]}" || rc=1
    say "${#uapi_h[@]} 个 UAPI 头 / ${#airy_c[@]} 个 .c / ${#airy_h[@]} 个 .h / ${#kbuild[@]} 个 Kbuild"
    endgroup
    [ "${rc}" -eq 0 ] || die "SPDX 归属断言失败"
    say "SPDX 归属断言通过"
}

case "${KIND}" in
assert) run_assert ;;
style) run_style ;;
*) arg_fail "未知 --kind: ${KIND:-<空>}" ;;
esac
