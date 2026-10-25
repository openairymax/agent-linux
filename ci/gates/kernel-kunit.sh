#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-kunit.sh — KUnit（UML）运行与基线比对
#
# 用法:
#   ci/gates/kernel-kunit.sh [--src DIR] [--tap FILE]
#
# KUnit 用 O=.kunit 外置构建，而 kbuild 要求源码树干净，因此这一步必须
# 排在所有 in-tree 构建之前。此前这个「顺序敏感」只写在 ci-kernel.yml 的
# 一条注释里，步骤一挪就静默失效（kunit.py 会报 kbuild 脏树或复用到旧
# 配置）。现在把前提变成前置断言：树里有 .config / vmlinux 就明确报错。
#
# 基线比对的语义由 tools/kunit-tap-diff.py 决定：用例集合与期望状态必须
# 与基线一致，新增/消失/翻转都算漂移。
#
# 退出码: 0 通过；1 运行失败或与基线漂移；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=kernel-kunit
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
TAP="kunit.tap"

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-kunit.sh [--src DIR] [--tap FILE]

  --tap  KUnit 原始 TAP 输出路径（缺省 kunit.tap，相对当前目录）
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --tap) TAP="${2:-}"; shift 2 ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"
[ -d "${SRC}/security/airy" ] || die "缺少 KUnit 配置目录: security/airy"

# 顺序断言：in-tree 构建产物会让 kunit.py 判定源码树不干净。
if [ -f "${SRC}/.config" ] || [ -f "${SRC}/vmlinux" ]; then
    die "源码树已有 in-tree 构建产物，KUnit 必须在所有 in-tree 构建之前运行"
fi

cd "${SRC}" || die "无法进入 ${SRC}"

group "KUnit on UML（OS-TEST-001~012）"
set -o pipefail
./tools/testing/kunit/kunit.py run --arch=um --raw_output=kunit \
    --kunitconfig=security/airy 2>&1 | tee "${TAP}"
rc=${PIPESTATUS[0]}
endgroup
[ "${rc}" -eq 0 ] || die "KUnit 运行失败（rc=${rc}）"

group "KUnit TAP 基线比对（OS-TEST-012）"
python3 "${REPO_ROOT}/tools/kunit-tap-diff.py" --current "${TAP}"
rc=$?
endgroup
[ "${rc}" -eq 0 ] || die "KUnit 结果与基线漂移"

say "KUnit 通过并与基线一致"
