#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_coupling_scan.sh — ci/gates/coupling-scan.sh 的分支回归
#
# 门禁判的是成册件 §10 主表与仓库实物两向对齐，真实树常年为绿，判定逻辑
# 只能靠负例驱动：逐条删主表行、删实物行、造未登记耦合点，断言只看退出码
# 与稳定判据名。成册件的总量统计也是判据之一：删行而不改总量必须判负。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh coupling-scan 负责那一条，
# 本文件只保证门禁没有漏判（未登记耦合被放过）与误判（已登记行被当作新增）。
#
# 用法: bash ci/tests/test_coupling_scan.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/coupling-scan.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/repo" \
    "${FX}/distro/lockfile" "${FX}/distro/kickstart" "${FX}/distro/specs" \
    "${FX}/ci/container" "${FX}/.github/workflows"
cp "${GATE}" "${FX}/ci/gates/coupling-scan.sh"
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
    bash "${FX}/ci/gates/coupling-scan.sh" --source "${FX}" "$@" 2>&1
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

ledger()
{
    local total="$1"
    cat >"${FX}/ledger.md" <<EOF
# 基底耦合点成册件（测试夹具）

## 9. 统计

| 维度 | 数量 |
|---|---|
| 耦合点总数 | ${total} |

## 10. 复算入口

BC-001|distro/repo/mirrors.list|^https://.*openEuler-24.03-LTS
BC-002|distro/repo/airymax-os.repo|openEuler-24.03-LTS
BC-003|distro/lockfile/base.yaml|^selected:
BC-004|distro/lockfile/base.yaml|^kernel_line:
BC-005|distro/lockfile/rpms.in.yaml|^[[:space:]]*baseurl:
BC-006|distro/kickstart/airymaxos-workstation.ks|(url --url|repo .*--baseurl=https)
BC-007|distro/specs/airymaxos-release.spec|^Obsoletes:.*openEuler
BC-008|ci/container/Containerfile.agent-base|^ARG BASE_IMAGE=
BC-009|.github/workflows/image-build.yml|runs-on: ubuntu-24.04
BC-010|.github/workflows/image-build.yml|OE_ISO_NAME: openEuler
EOF
}

mkfix()
{
    ledger 10
    cat >"${FX}/distro/repo/mirrors.list" <<'EOF'
https://repo.openeuler.org/openEuler-24.03-LTS
https://mirrors.nju.edu.cn/openeuler/openEuler-24.03-LTS
EOF
    cat >"${FX}/distro/repo/airymax-os.repo" <<'EOF'
[airymaxos-base]
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS/OS/$basearch/
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS/update/$basearch/
EOF
    cat >"${FX}/distro/lockfile/base.yaml" <<'EOF'
selected: openEuler-24.03-LTS
kernel_line: 6.6
EOF
    cat >"${FX}/distro/lockfile/rpms.in.yaml" <<'EOF'
repos:
  - name: base
    baseurl: https://repo.openeuler.org/openEuler-24.03-LTS/OS/$basearch/
EOF
    cat >"${FX}/distro/kickstart/airymaxos-workstation.ks" <<'EOF'
url --url=https://repo.openeuler.org/openEuler-24.03-LTS/OS/$basearch/
repo --name=update --baseurl=https://repo.openeuler.org/openEuler-24.03-LTS/update/$basearch/
EOF
    cat >"${FX}/distro/specs/airymaxos-release.spec" <<'EOF'
Name: airymaxos-release
Obsoletes: openEuler-release < 24.04
EOF
    cat >"${FX}/ci/container/Containerfile.agent-base" <<'EOF'
ARG BASE_IMAGE=openeuler:24.03-lts
EOF
    cat >"${FX}/.github/workflows/image-build.yml" <<'EOF'
env:
  OE_ISO_NAME: openEuler-24.03-LTS-netinst-x86_64-dvd.iso
jobs:
  build:
    runs-on: ubuntu-24.04
EOF
    rm -f "${FX}/distro/specs/new.spec"
}

mkfix
out="$(run --ledger "${FX}/ledger.md")"
check "主表与实物对齐判通过" 0 "$?" "${out}" "coupling: 10 declared, 10 found, delta 0"

mkfix
mkdir -p "${tmp}/docs/docs-closed/agent-linux-dev/10-references"
cp "${FX}/ledger.md" "${tmp}/docs/docs-closed/agent-linux-dev/10-references/baseline-coupling.md"
out="$(bash "${FX}/ci/gates/coupling-scan.sh" --source "${FX}" 2>&1)"
check "默认成册件路径可解析" 0 "$?" "${out}" "coupling: 10 declared, 10 found, delta 0"
rm -rf "${tmp}/docs"

mkfix
sed -i '/^BC-003|/d' "${FX}/ledger.md"
out="$(run --ledger "${FX}/ledger.md")"
check "删主表行不改总量判拒绝" 1 "$?" "${out}" "ledger/total"

mkfix
sed -i '/^BC-003|/d' "${FX}/ledger.md"
sed -i 's/^| 耦合点总数 | 10 |$/| 耦合点总数 | 9 |/' "${FX}/ledger.md"
out="$(run --ledger "${FX}/ledger.md")"
check "删主表行且改总量仍判拒绝" 1 "$?" "${out}" "coupling/undeclared distro/lockfile/base.yaml:1"

mkfix
printf 'baseurl=https://repo.centos.org/centos/9-stream/BaseOS/$basearch/\n' \
    >>"${FX}/distro/repo/airymax-os.repo"
out="$(run --ledger "${FX}/ledger.md")"
check "新增未登记仓库判拒绝" 1 "$?" "${out}" "coupling/undeclared distro/repo/airymax-os.repo:4"

mkfix
printf 'BuildRequires: openEuler-release\n' >"${FX}/distro/specs/new.spec"
out="$(run --ledger "${FX}/ledger.md")"
check "新增未登记 spec 文件判拒绝" 1 "$?" "${out}" "coupling/undeclared distro/specs/new.spec:1"

mkfix
sed -i '/runs-on:/d' "${FX}/.github/workflows/image-build.yml"
out="$(run --ledger "${FX}/ledger.md")"
check "已登记耦合实物消失判拒绝" 1 "$?" "${out}" "coupling/missing BC-009"

mkfix
sed -i '/OE_ISO_NAME:/d' "${FX}/.github/workflows/image-build.yml"
out="$(run --ledger "${FX}/ledger.md")"
check "介质名消失判拒绝" 1 "$?" "${out}" "coupling/missing BC-010"

mkfix
printf '# baseurl=https://repo.centos.org/centos/9-stream/\n' \
    >>"${FX}/distro/repo/airymax-os.repo"
out="$(run --ledger "${FX}/ledger.md")"
check "注释行不计入未登记耦合" 0 "$?" "${out}" "delta 0"

mkfix
out="$(run --ledger "${FX}/ledger.md" --json)"
check "JSON 对齐判定通过" 0 "$?" "${out}" '"declared":10,"found":10,"delta":0'
case "${out}" in *'"missing":[]'*) ok "JSON 空 missing 契约" ;;
*) bad "JSON 空 missing 契约（${out}）" ;;
esac
case "${out}" in *'"undeclared":[]'*) ok "JSON 空 undeclared 契约" ;;
*) bad "JSON 空 undeclared 契约（${out}）" ;;
esac

mkfix
printf 'baseurl=https://repo.centos.org/centos/9-stream/BaseOS/$basearch/\n' \
    >>"${FX}/distro/repo/airymax-os.repo"
out="$(run --ledger "${FX}/ledger.md" --json)"
check "JSON 对账失败退 1" 1 "$?" "${out}" '"undeclared":["distro/repo/airymax-os.repo:4"]'

out="$(run --ledger "${tmp}/nope.md")"
check "成册件不存在退 5" 5 "$?" "${out}" "成册件不存在"

out="$(run --ledger)"
check "缺 --ledger 参数退 5" 5 "$?" "${out}" "需要文件路径"

out="$(bash "${FX}/ci/gates/coupling-scan.sh" --source "${tmp}/nope" 2>&1)"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(bash "${FX}/ci/gates/coupling-scan.sh" --source "${FX}" 2>&1)"
check "无成册件时跳过不假绿" 0 "$?" "${out}" "本门禁不适用"

if [ "${fails}" -ne 0 ]; then
    printf 'test_coupling_scan: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_coupling_scan: 全部通过\n'
