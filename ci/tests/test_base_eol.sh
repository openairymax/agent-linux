#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_base_eol.sh — ci/gates/base-eol.sh 的分支回归
#
# 门禁判的是基底选型台账与全部消费面同源，真实树常年为绿，判定逻辑只能
# 用负例驱动：逐条破坏规则、仓库地址、镜像根、冻结输入、构建宏、安装配方
# 与说明面，断言只看退出码与稳定判据名。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh base-eol 负责那一条，本文件
# 只保证门禁没有漏判与误判。
#
# 用法: bash ci/tests/test_base_eol.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/base-eol.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/lockfile" \
    "${FX}/distro/repo" "${FX}/distro/specs" "${FX}/distro/kickstart" \
    "${FX}/distro/comps" "${FX}/distro/ostree" "${FX}/distro/desktop" \
    "${FX}/distro/tools"
cp "${GATE}" "${FX}/ci/gates/base-eol.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/distro/lockfile/base_check.py" "${FX}/distro/lockfile/"
cp "${ROOT}/distro/lockfile/lineyaml.py" "${FX}/distro/lockfile/"

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
    bash "${FX}/ci/gates/base-eol.sh" "$@" 2>&1
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

base()
{
    cat >"${FX}/distro/lockfile/base.yaml" <<'EOF'
deadline: 2027-09-17
selected: openEuler-24.03-LTS-SP3
arch: x86_64
kernel_line: 6.6

policy:
  kind: large-sp-only
  eol-covers: 2027-09-17

candidates:
  - name: openEuler-24.03-LTS-SP1
    ga: 2024-12-30
    kind: large
    eol: 2026-12-30
    kernel: 6.6
  - name: openEuler-24.03-LTS-SP2
    ga: 2025-06-30
    kind: small
    eol: 2026-03-30
    kernel: 6.6
  - name: openEuler-24.03-LTS-SP3
    ga: 2025-12-30
    kind: large
    eol: 2027-12-30
    kernel: 6.6
  - name: openEuler-24.03-LTS-SP4
    ga: 2026-06-30
    kind: small
    eol: 2027-03-30
    kernel: 6.6
EOF
}

repo()
{
    cat >"${FX}/distro/repo/airymax-os.repo" <<'EOF'
[airymaxos-base]
name=AirymaxOS Base
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/OS/$basearch/ https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP3/OS/$basearch/ https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP3/OS/$basearch/

[airymaxos-updates]
name=AirymaxOS Updates
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/update/$basearch/ https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP3/update/$basearch/ https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP3/update/$basearch/

[airymaxos-epol]
name=AirymaxOS EPOL
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/EPOL/main/$basearch/ https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP3/EPOL/main/$basearch/ https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP3/EPOL/main/$basearch/

[airymaxos-epol-updates]
name=AirymaxOS EPOL Updates
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/EPOL/update/main/$basearch/ https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP3/EPOL/update/main/$basearch/ https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP3/EPOL/update/main/$basearch/

[airymaxos-everything]
name=AirymaxOS Everything
baseurl=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/everything/$basearch/ https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP3/everything/$basearch/ https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP3/everything/$basearch/
EOF
}

mirrors()
{
    cat >"${FX}/distro/repo/mirrors.list" <<'EOF'
https://repo.openeuler.org/openEuler-24.03-LTS-SP3
https://mirrors.nju.edu.cn/openeuler/openEuler-24.03-LTS-SP3
https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP3
https://mirrors.ustc.edu.cn/openeuler/openEuler-24.03-LTS-SP3
https://mirrors.aliyun.com/openeuler/openEuler-24.03-LTS-SP3
https://mirrors.cloud.tencent.com/openeuler/openEuler-24.03-LTS-SP3
https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP3
EOF
}

seeds()
{
    cat >"${FX}/distro/lockfile/rpms.in.yaml" <<'EOF'
packages:
  - bash
repos:
  - name: base
    baseurl: https://repo.openeuler.org/openEuler-24.03-LTS-SP3/OS/$basearch/
  - name: updates
    baseurl: https://repo.openeuler.org/openEuler-24.03-LTS-SP3/update/$basearch/
  - name: epol
    baseurl: https://repo.openeuler.org/openEuler-24.03-LTS-SP3/EPOL/main/$basearch/
  - name: epol-updates
    baseurl: https://repo.openeuler.org/openEuler-24.03-LTS-SP3/EPOL/update/main/$basearch/
  - name: everything
    baseurl: https://repo.openeuler.org/openEuler-24.03-LTS-SP3/everything/$basearch/
EOF
}

spec()
{
    cat >"${FX}/distro/specs/airymaxos-release.spec" <<'EOF'
%global oe_base openEuler-24.03-LTS-SP3

Name: airymaxos-release

%changelog
* Thu Oct 01 2026 Liren Wang <wangliren@spharx.cn> - 0.0.4-1
- Base on openEuler 24.03 LTS SP2.
EOF
}

ukui()
{
    cat >"${FX}/distro/lockfile/ukui-components.yaml" <<'EOF'
base: openEuler-24.03-LTS-SP3
components: []
EOF
}

ks()
{
    cat >"${FX}/distro/kickstart/airymaxos-workstation.ks" <<'EOF'
url --url=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/OS/$basearch/
repo --name=update --baseurl=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/update/$basearch/
EOF
    cat >"${FX}/distro/kickstart/airymaxos-minimal.ks" <<'EOF'
url --url=https://repo.openeuler.org/openEuler-24.03-LTS-SP3/OS/$basearch/
EOF
}

notes()
{
    local pair
    for pair in \
        "comps/airymaxos-comps.xml:# openEuler 24.03 LTS-SP3 EPOL/main" \
        "ostree/airymaxos-workstation.treefile:  - openEuler-24.03-LTS-SP3" \
        "desktop/README.md:> 基底 openEuler 24.03 LTS-SP3" \
        "desktop/70-airymax-ukui-policy.sh:# openEuler 24.03 LTS-SP3" \
        "tools/cfg_brand.py:# openEuler 24.03-LTS-SP3 netinst"; do
        printf '%s\n' "${pair#*:}" >"${FX}/distro/${pair%%:*}"
    done
}

mkfix()
{
    base
    repo
    mirrors
    seeds
    spec
    ukui
    ks
    notes
}

mkfix

out="$(run)"
check "默认源树合规判定通过" 0 "$?" "${out}" "覆盖收口日"

out="$(run --source "${FX}")"
check "显式源树合规判定通过" 0 "$?" "${out}" "基底选型同源通过"

out="$(run --source "${tmp}/nope")"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

mv "${FX}/distro/lockfile/base_check.py" "${tmp}/base_check.py"
out="$(run --source "${FX}")"
check "缺少校验器退 5" 5 "$?" "${out}" "缺少校验器"
mv "${tmp}/base_check.py" "${FX}/distro/lockfile/base_check.py"

mkfix
rm -f "${FX}/distro/lockfile/base.yaml"
out="$(run --source "${FX}")"
check "缺少基底台账判拒绝" 1 "$?" "${out}" "schema/base-missing"

mkfix
sed -i 's/^selected: .*/selected: openEuler-24.03-LTS-SP2/' \
    "${FX}/distro/lockfile/base.yaml"
out="$(run --source "${FX}")"
check "小 SP 选中判拒绝" 1 "$?" "${out}" "base/small-sp"

mkfix
sed -i 's/^selected: .*/selected: openEuler-24.03-LTS-SP1/' \
    "${FX}/distro/lockfile/base.yaml"
out="$(run --source "${FX}")"
check "EOL 不覆盖收口日判拒绝" 1 "$?" "${out}" "base/eol"

mkfix
sed -i '/name: openEuler-24.03-LTS-SP4/,/kernel:/{s/kind: small/kind: large/;s/eol: 2027-03-30/eol: 2027-12-30/}' \
    "${FX}/distro/lockfile/base.yaml"
out="$(run --source "${FX}")"
check "合格候选未取 GA 最晚判拒绝" 1 "$?" "${out}" "base/not-newest"

mkfix
sed -i 's/^selected: .*/selected: openEuler-24.03-LTS-SP9/' \
    "${FX}/distro/lockfile/base.yaml"
out="$(run --source "${FX}")"
check "选中项不在候选表判拒绝" 1 "$?" "${out}" "base/unknown"

mkfix
sed -i '0,/openEuler-24.03-LTS-SP3/s//openEuler-24.03-LTS-SP2/' \
    "${FX}/distro/repo/airymax-os.repo"
out="$(run --source "${FX}")"
check "仓库地址漂移判拒绝" 1 "$?" "${out}" "repo/baseurl"

mkfix
sed -i '/^\[airymaxos-everything\]/,/^$/d' \
    "${FX}/distro/repo/airymax-os.repo"
out="$(run --source "${FX}")"
check "仓库段缺失判拒绝" 1 "$?" "${out}" "repo/count"

mkfix
sed -i '0,/openEuler-24.03-LTS-SP3/s//\$releasever/' \
    "${FX}/distro/repo/airymax-os.repo"
out="$(run --source "${FX}")"
check "仓库地址使用 releasever 判拒绝" 1 "$?" "${out}" "repo/releasever"

mkfix
sed -i 's|/openEuler-24.03-LTS-SP3$|/openEuler-24.03-LTS-SP2|' \
    "${FX}/distro/repo/mirrors.list"
out="$(run --source "${FX}")"
check "镜像根漂移判拒绝" 1 "$?" "${out}" "mirrors/root"

mkfix
sed -i '0,/openEuler-24.03-LTS-SP3/s//openEuler-24.03-LTS-SP2/' \
    "${FX}/distro/lockfile/rpms.in.yaml"
out="$(run --source "${FX}")"
check "冻结输入漂移判拒绝" 1 "$?" "${out}" "seeds/baseurl"

mkfix
sed -i 's/^%global oe_base .*/%global oe_base openEuler-24.03-LTS-SP2/' \
    "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --source "${FX}")"
check "构建宏漂移判拒绝" 1 "$?" "${out}" "spec/base"

mkfix
sed -i '1a # 早期取 openEuler-24.03-LTS-SP2' \
    "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --source "${FX}")"
check "changelog 之前出现旧基底判拒绝" 1 "$?" "${out}" "spec/token"

mkfix
sed -i 's/^base: .*/base: openEuler-24.03-LTS-SP2/' \
    "${FX}/distro/lockfile/ukui-components.yaml"
out="$(run --source "${FX}")"
check "UKUI 台账基底漂移判拒绝" 1 "$?" "${out}" "ukui/base"

mkfix
sed -i 's|/openEuler-24.03-LTS-SP3/|/openEuler-24.03-LTS-SP2/|' \
    "${FX}/distro/kickstart/airymaxos-minimal.ks"
out="$(run --source "${FX}")"
check "安装配方漂移判拒绝" 1 "$?" "${out}" "ks/base"

mkfix
sed -i 's/LTS-SP3/LTS-SP2/' "${FX}/distro/desktop/README.md"
out="$(run --source "${FX}")"
check "说明面基底漂移判拒绝" 1 "$?" "${out}" "token/drift"

mkfix
rm -f "${FX}/distro/repo/mirrors.list"
out="$(run --source "${FX}")"
check "缺少消费面文件判拒绝" 1 "$?" "${out}" "file/missing"

if [ "${fails}" -ne 0 ]; then
    printf 'test_base_eol: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_base_eol: 全部通过\n'
