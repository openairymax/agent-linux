#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_mgmt_files.sh — ci/gates/mgmt-files.sh 废弃前缀判据的回归
#
# 这条判据此前用裸子串 grep "airymaxos-"，把 GHCR 的 OCI 包名 airymaxos-iso
# 与发行产物名 airymaxos-<ver>-x86_64.iso 一并判红：真树上的 README 里这两
# 个标识符都是合法交付物，于是门禁长期恒红、无人再读。判据收窄成「仓库命名」
# 之后，正例（产物名放行）与负例（裸名带前缀判红）都必须被钉住，否则下一次
# 更容易收着收着又变成「什么都不判」。
#
# 整树断言（子模块检出、[SC] 头文件、CODEOWNERS 投影）在假树上无从成立，
# 因此只经 --only 驱动单项；真树是否合规由 mgmt-verify 那条负责。
#
# 用法: bash ci/tests/test_mgmt_files.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
fails=0

mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${ROOT}/ci/gates/mgmt-files.sh" "${FX}/ci/gates/mgmt-files.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${FX}/ci/gates/mgmt-files.sh" "$@" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

# tree — 复刻真树的形状：.gitmodules 声明 9 个子仓，三份治理文件都在，
# README 里带着合法产物名（GHCR 包与 ISO 文件名）。禁止集合只从这里派生，
# 所以裸名清单不写在测试里，避免出现第二份名单。
tree()
{
    rm -rf "${FX}/.gitmodules" "${FX}/README.md" "${FX}/README_zh.md" \
        "${FX}/MAINTAINERS"
    cat >"${FX}/.gitmodules" <<'EOF'
[submodule "kernel"]
	path = kernel
	url = ../kernel.git
[submodule "memory"]
	path = memory
	url = ../memory.git
[submodule "security"]
	path = security
	url = ../security.git
[submodule "cognition"]
	path = cognition
	url = ../cognition.git
[submodule "services"]
	path = services
	url = ../services.git
[submodule "system"]
	path = system
	url = ../system.git
[submodule "cloudnative"]
	path = cloudnative
	url = ../cloudnative.git
[submodule "tests-linux"]
	path = tests-linux
	url = ../tests-linux.git
[submodule "distro"]
	path = distro
	url = ../distro.git
EOF
    cat >"${FX}/README.md" <<'EOF'
# AirymaxOS

- 镜像: ghcr.io/openairymax/airymaxos-iso:v0.0.4
- 产物: releases/v0.0.4/airymaxos-<ver>-x86_64.iso
EOF
    printf '# AirymaxOS\n\n- 镜像: airymaxos-iso\n' >"${FX}/README_zh.md"
    printf 'Liren Wang <wangliren@spharx.cn>\n' >"${FX}/MAINTAINERS"
}

tree
out="$(run --only no-prefix)"
check "产物名放行" 0 "$?" "${out}" "无废弃的 airymaxos- 前缀（裸名 9 个）"

tree
printf '\n- 子仓: airymaxos-kernel\n' >>"${FX}/README.md"
out="$(run --only no-prefix)"
check "裸名带前缀判拒绝" 1 "$?" "${out}" "已废弃的 airymaxos- 前缀"

tree
printf '\n- 子仓: airymaxos-distro\n' >>"${FX}/README_zh.md"
out="$(run --only no-prefix)"
check "中文 README 同样判拒绝" 1 "$?" "${out}" "已废弃的 airymaxos- 前缀"

tree
printf 'airymaxos-kernel-6.6 <k@spharx.cn>\n' >>"${FX}/MAINTAINERS"
out="$(run --only no-prefix)"
check "裸名带版本后缀判拒绝" 1 "$?" "${out}" "已废弃的 airymaxos- 前缀"

# 未在 .gitmodules 声明的名字不属于「仓库命名」，放行；一旦真的新增该子仓，
# .gitmodules 会带上它，判据自动收紧。
tree
printf '\n- 其它: airymaxos-kernels\n' >>"${FX}/README.md"
out="$(run --only no-prefix)"
check "未声明的近似名放行" 0 "$?" "${out}" "无废弃的 airymaxos- 前缀"

tree
rm -f "${FX}/README_zh.md"
out="$(run --only no-prefix)"
check "缺被检文件判拒绝" 1 "$?" "${out}" "缺少被检文件: README_zh.md"

tree
: >"${FX}/.gitmodules"
out="$(run --only no-prefix)"
check "空 .gitmodules 判拒绝" 1 "$?" "${out}" "未声明任何子模块"

out="$(run --only bogus)"
check "未知检查项退 5" 5 "$?" "${out}" "未知检查项: bogus"

out="$(run --only)"
check "缺 --only 参数退 5" 5 "$?" "${out}" "用法:"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数: --bogus"

out="$(run --help)"
check "帮助退 0" 0 "$?" "${out}" "用法:"

if [ "${fails}" -ne 0 ]; then
    printf 'test_mgmt_files: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_mgmt_files: 全部通过\n'
