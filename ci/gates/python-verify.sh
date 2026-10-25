#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/python-verify.sh — tools/ 下 Python 工具链的统一门禁
#
# 用法:
#   ci/gates/python-verify.sh --kind abi|codegen|dsl|farm|oschk
#
# 每个 kind 对应一组 requirements-dev.txt、若干 pytest 目录，以及（多数
# kind）一条漂移检查命令。这些步骤此前散在 4 个 workflow 的内联 shell 里：
# 同一份依赖装在 4 处各写一遍，改一处要跑 4 条云端流水线才能确认，本地
# 无法复现。收进 gate 后，runner 与开发者执行的是同一条命令。
#
# 退出码: 0 通过；1 检查失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=python-verify
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/python-verify.sh --kind abi|codegen|dsl|farm|oschk
EOF
}

KIND=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --kind) KIND="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${KIND}" ] || { usage >&2; exit "${EINVAL}"; }

REQS=()
TESTS=()
CHECK=()
HINT=""

case "${KIND}" in
    abi)
        REQS=(tools/abi-check/requirements-dev.txt)
        TESTS=(tools/abi-check/tests/)
        CHECK=(python3 tools/abi-check/abi_check.py --check)
        HINT="syscall ABI 漂移（OS-IRON-001）：核对四个权威源与 abi_baseline.json"
        ;;
    codegen)
        REQS=(tools/codegen/requirements-dev.txt)
        TESTS=(tools/codegen/tests/)
        CHECK=(python3 tools/codegen/run_codegen.py --check)
        HINT="codegen 产物漂移（R-01/R-02/IRON-9）：本地跑 make codegen 后提交结果"
        ;;
    dsl)
        # dsl 单向复用 codegen 的确定性渲染环境（codegen_io / codegen_types），
        # 依赖取自 codegen 的 requirements-dev.txt。
        REQS=(tools/codegen/requirements-dev.txt)
        TESTS=(tools/dsl/tests/)
        CHECK=(python3 tools/dsl/dsl_compile.py --check)
        HINT="策略 DSL 产物漂移（IRON-9）：本地跑 make -C tools/dsl 后提交结果"
        ;;
    farm)
        REQS=(tools/airy-qemu/requirements-dev.txt)
        TESTS=(tools/airy-qemu/tests/)
        HINT="QEMU farm 单元与端到端用例"
        ;;
    oschk)
        # OS-CHK 的检查器逐个复用下列工具的入口；缺任一依赖，对应门禁在
        # CI 恒为 ERROR 而本地不可见，故依赖必须在此装齐。
        REQS=(
            tools/os-chk/requirements-dev.txt
            tools/codegen/requirements-dev.txt
            tools/defconfig-matrix/requirements-dev.txt
            tools/ci-matrix/requirements-dev.txt
            tools/btf-integration/requirements-dev.txt
            tools/kbuild-version/requirements-dev.txt
            tools/release-engineering/requirements-dev.txt
            tools/nightly/requirements-dev.txt
            tools/repo-scaffold/requirements-dev.txt
            tools/airy-rollback/requirements-dev.txt
        )
        TESTS=(
            tools/os-chk/tests/
            tools/airy-checkpatch/tests/
            tools/defconfig-matrix/tests/
            tools/ci-matrix/tests/
            tools/btf-integration/tests/
            tools/kbuild-version/tests/
            tools/release-engineering/tests/
            tools/nightly/tests/
            tools/repo-scaffold/tests/
            tools/airy-rollback/tests/
            tools/osaccrt/tests/
        )
        CHECK=(python3 tools/os-chk/os_chk.py --check)
        HINT="OS-CHK 统一门禁（P0/P1 阻断）：python3 tools/os-chk/os_chk.py --verbose"
        ;;
    *)
        err "--kind 只接受 abi|codegen|dsl|farm|oschk"
        exit "${EINVAL}"
        ;;
esac

cd "${ROOT}" || die "无法进入仓库根: ${ROOT}"

# 本地 python3 常被发行版拆掉 pip/ensurepip；先给出可执行的提示，而不是让
# 第一条 pip 命令以 "No module named pip" 失败。
if ! python3 -m pip --version >/dev/null 2>&1; then
    die "本机 python3 没有 pip：请先 'python3 -m venv .venv && . .venv/bin/activate'，或安装 python3-pip"
fi

group "${KIND}: 依赖"
for r in "${REQS[@]}"; do
    [ -f "${r}" ] || die "缺少依赖清单: ${r}"
    python3 -m pip install -q -r "${r}" || die "依赖安装失败: ${r}"
done
say "已安装 ${#REQS[@]} 份依赖清单"
endgroup

group "${KIND}: pytest"
python3 -m pytest "${TESTS[@]}" -q || die "pytest 失败（${KIND}）"
endgroup

if [ "${#CHECK[@]}" -gt 0 ]; then
    group "${KIND}: 漂移检查"
    "${CHECK[@]}" || die "${HINT}"
    say "通过：${HINT}"
    endgroup
fi
