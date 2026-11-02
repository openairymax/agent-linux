#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_ukui_lock.sh — ci/gates/ukui-lock.sh 的分支回归
#
# 门禁判的是四份文件同源（台账 × comps × 冻结输入 × 取证册），真实树常年
# 为绿，判定逻辑只能用负例驱动：逐条破坏 schema、核心件状态、NVRA、comps
# 的归属与重复、冻结覆盖、取证册记录，断言只看退出码与稳定判据名。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh ukui-lock 负责那一条，本文件
# 只保证门禁没有漏判与误判。
#
# 用法: bash ci/tests/test_ukui_lock.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/ukui-lock.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
DOC="${tmp}/docs"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/lockfile" \
    "${FX}/distro/comps" "${DOC}"
cp "${GATE}" "${FX}/ci/gates/ukui-lock.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/distro/lockfile/ukui_check.py" "${FX}/distro/lockfile/"

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
    bash "${FX}/ci/gates/ukui-lock.sh" "$@" 2>&1
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
    cat >"${FX}/distro/lockfile/ukui-components.yaml" <<'EOF'
base: openEuler-24.03-LTS-SP2
arch: x86_64
segments:
  - OS
  - update
  - EPOL/main
  - EPOL/update/main
  - everything

policy:
  route: base-highest-stable
  generation: 3.1
  deferred: ukui-4.24

components:
  - name: ukui-kwin
    state: present
    segment: EPOL/main
    nvra: ukui-kwin-0:1.0.5-8.oe2403sp2.x86_64
  - name: ukui-panel
    state: present
    segment: EPOL/main
    nvra: ukui-panel-0:3.1.0-14.oe2403sp2.x86_64
  - name: ukui-session-manager
    state: present
    segment: EPOL/main
    nvra: ukui-session-manager-0:3.1.0-7.oe2403sp2.x86_64
  - name: ukui-control-center
    state: present
    segment: EPOL/main
    nvra: ukui-control-center-0:3.1.2-33.oe2403sp2.x86_64
  - name: ukui-menu
    state: present
    segment: EPOL/main
    nvra: ukui-menu-0:3.1.1-14.oe2403sp2.x86_64
  - name: libpam-biometric
    state: present
    segment: EPOL/main
    nvra: libpam-biometric-0:1.2.4-2.oe2403sp2.x86_64
  - name: ukui-desktop-environment
    state: available
    segment: EPOL/main
    nvra: ukui-desktop-environment-0:2.0.2-20.oe2403sp2.x86_64
  - name: ukui
    state: available
    segment: EPOL/main
    nvra: ukui-0:2.0.2-20.oe2403sp2.x86_64
  - name: ukui-shell
    state: absent
    segment: 五段均无
EOF
}

comps()
{
    cat >"${FX}/distro/comps/airymaxos-comps.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<comps>
  <group>
    <id>airymax-core</id>
    <packagelist>
      <packagereq type="mandatory">ukui-kwin</packagereq>
      <packagereq type="mandatory">ukui-panel</packagereq>
      <packagereq type="mandatory">ukui-session-manager</packagereq>
      <packagereq type="mandatory">ukui-control-center</packagereq>
    </packagelist>
  </group>
  <group>
    <id>airymax-desktop</id>
    <packagelist>
      <packagereq type="default">ukui-menu</packagereq>
    </packagelist>
  </group>
  <group>
    <id>airymax-desktop-extras</id>
    <packagelist>
      <packagereq type="optional">libpam-biometric</packagereq>
    </packagelist>
  </group>
  <environment>
    <id>airymax-workstation</id>
    <grouplist>
      <groupid>airymax-core</groupid>
      <groupid>airymax-desktop</groupid>
    </grouplist>
    <optionlist>
      <groupid>airymax-desktop-extras</groupid>
    </optionlist>
  </environment>
</comps>
EOF
}

seeds()
{
    cat >"${FX}/distro/lockfile/rpms.in.yaml" <<'EOF'
packages:
  - ukui-kwin
  - ukui-panel
  - ukui-session-manager
  - ukui-control-center
  - ukui-menu
repos:
  - name: epol
EOF
}

docs()
{
    {
        printf '# 差异清单\n\n'
        printf '| 组件 | NVRA |\n|---|---|\n'
        printf '| kwin | `ukui-kwin-0:1.0.5-8.oe2403sp2.x86_64` |\n'
        printf '| panel | `ukui-panel-0:3.1.0-14.oe2403sp2.x86_64` |\n'
        printf '| session | `ukui-session-manager-0:3.1.0-7.oe2403sp2.x86_64` |\n'
        printf '| ccc | `ukui-control-center-0:3.1.2-33.oe2403sp2.x86_64` |\n'
        printf '| menu | `ukui-menu-0:3.1.1-14.oe2403sp2.x86_64` |\n'
        printf '| bio | `libpam-biometric-0:1.2.4-2.oe2403sp2.x86_64` |\n\n'
        printf '```sh\ndnf -q repoquery --repo=epol "ukui-*"\n```\n'
    } >"${DOC}/ukui-delta.md"
    cat >"${DOC}/ukui-424-feasibility.md" <<'EOF'
# 可行性

路线 A：自建 4.24 闭包。
路线 B：取基底 3.1 组件代。
基底无 ukui-shell；4.24 顺延。
EOF
}

mkfix()
{
    ledger
    comps
    seeds
    docs
}

mkfix

out="$(run)"
check "默认源树合规判定通过" 0 "$?" "${out}" "UKUI 台账同源通过"

out="$(run --source "${FX}")"
check "显式源树合规判定通过" 0 "$?" "${out}" "UKUI 台账同源通过"

out="$(run --source "${tmp}/nope")"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run --docs "${tmp}/nope")"
check "取证册目录不存在退 5" 5 "$?" "${out}" "目录不存在"

mv "${FX}/distro/lockfile/ukui_check.py" "${tmp}/ukui_check.py"
out="$(run --source "${FX}")"
check "缺少校验器退 5" 5 "$?" "${out}" "缺少校验器"
mv "${tmp}/ukui_check.py" "${FX}/distro/lockfile/ukui_check.py"

mkfix
sed -i 's/^base: .*/base: openEuler-24.03-LTS-SP3/' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "基底版本漂移判拒绝" 1 "$?" "${out}" "schema/base"

mkfix
sed -i 's/^  generation: .*/  generation: 4.24/' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "组件代漂移判拒绝" 1 "$?" "${out}" "schema/policy"

mkfix
sed -i '/name: ukui-kwin/,/nvra:/{s/state: present/state: absent/}' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "核心件未登记 present 判拒绝" 1 "$?" "${out}" "ledger/core"

mkfix
sed -i 's/^    nvra: ukui-panel-0:3.1.0-14/    nvra: ukui-panelx-0:3.1.0-14/' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "NVRA 与包名不符判拒绝" 1 "$?" "${out}" "ledger/nvra"

mkfix
sed -i 's|<packagereq type="default">ukui-menu</packagereq>|&\n      <packagereq type="optional">ukui-shell</packagereq>|' \
    "${FX}/distro/comps/airymaxos-comps.xml"
out="$(run --source "${FX}")"
check "comps 出现台账外包名判拒绝" 1 "$?" "${out}" "comps/phantom"

mkfix
sed -i 's|<packagereq type="mandatory">ukui-control-center</packagereq>|&\n      <packagereq type="default">ukui-menu</packagereq>|' \
    "${FX}/distro/comps/airymaxos-comps.xml"
out="$(run --source "${FX}")"
check "组件在多个组重复声明判拒绝" 1 "$?" "${out}" "comps/dup"

mkfix
sed -i '\|<packagereq type="mandatory">ukui-panel</packagereq>|d' \
    "${FX}/distro/comps/airymaxos-comps.xml"
out="$(run --source "${FX}")"
check "台账组件未进任何组判拒绝" 1 "$?" "${out}" "comps/missing"

mkfix
sed -i '\|  - ukui-menu$|d' "${FX}/distro/lockfile/rpms.in.yaml"
out="$(run --source "${FX}")"
check "默认安装集未冻结判拒绝" 1 "$?" "${out}" "seeds/missing"

mkfix
sed -i '/name: ukui-desktop-environment/,/nvra:/{s/state: available/state: present/}' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "元包被选进闭包判拒绝" 1 "$?" "${out}" "ledger/available"

mkfix
sed -i '/name: ukui-shell/,/segment:/{s/state: absent/state: available/}' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "不存在的包被登记为可用判拒绝" 1 "$?" "${out}" "ledger/absent"

mkfix
sed -i 's/^  - ukui-menu$/  - ukui-menu\n  - ukui-shell/' \
    "${FX}/distro/lockfile/rpms.in.yaml"
out="$(run --source "${FX}")"
check "冻结输入出现台账外包名判拒绝" 1 "$?" "${out}" "seeds/phantom"

mkfix
out="$(run --source "${FX}" --docs "${DOC}")"
check "取证册同源判定通过" 0 "$?" "${out}" "UKUI 台账同源通过"

mkfix
sed -i 's/ukui-kwin-0:1.0.5-8/ukui-kwin-0:1.0.5-9/' "${DOC}/ukui-delta.md"
out="$(run --source "${FX}" --docs "${DOC}")"
check "取证册 NVRA 漂移判拒绝" 1 "$?" "${out}" "docs/nvra"

mkfix
sed -i '/dnf -q repoquery/d' "${DOC}/ukui-delta.md"
out="$(run --source "${FX}" --docs "${DOC}")"
check "取证册缺少复算命令判拒绝" 1 "$?" "${out}" "docs/recalc"

mkfix
sed -i '/路线 B/d' "${DOC}/ukui-424-feasibility.md"
out="$(run --source "${FX}" --docs "${DOC}")"
check "可行性册缺取舍结论判拒绝" 1 "$?" "${out}" "docs/feas"

mkfix
rm -f "${DOC}/ukui-424-feasibility.md"
out="$(run --source "${FX}" --docs "${DOC}")"
check "取证册缺册判拒绝" 1 "$?" "${out}" "docs/missing"

if [ "${fails}" -ne 0 ]; then
    printf 'test_ukui_lock: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_ukui_lock: 全部通过\n'
