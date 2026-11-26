#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_release_005.sh — ci/gates/release-005.sh 的分支回归
#
# 发版自证判的是「这一版能不能挂 tag」，真实树在版本 bump 之前必然为红，
# 所以判定逻辑只能用夹具驱动：版本三处不同源、tag 与树内版本不同源、
# 工作区出现第二个 Cargo.lock、vendor 三件缺一、工具链钉版漂移、门禁脚本
# 缺失、打包清单缺条目或多出软链，逐条造出并断言判据名。
#
# rpm 用 PATH 里的替身喂清单：判据消费的是 rpm -qlpv 的输出格式，不是 rpm
# 本身；替身只出现在测试进程的 PATH 里，产品路径上没有任何伪实现。
#
# 用法: bash ci/tests/test_release_005.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
OUT="${tmp}/out"

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
    ( cd "${FX}" && env -u LD_LIBRARY_PATH PATH="${tmp}/bin:${PATH}" \
        bash ci/gates/release-005.sh --out "${OUT}" "$@" 2>&1 )
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

# tree —— 重建一棵「四件套齐备且版本同源」的最小仓库。
tree()
{
    rm -rf "${FX}" "${tmp}/rpm"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/release" \
        "${FX}/distro/specs" "${FX}/system/tui/.cargo" "${FX}/system/tui/vendor" \
        "${FX}/system/units" "${tmp}/bin" "${tmp}/rpm"
    cp "${ROOT}/ci/gates/release-005.sh" "${FX}/ci/gates/release-005.sh"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
    printf 'readonly PIN=1.90.0\n' >"${FX}/ci/lib/tui_gate.sh"
    printf 'VERSION_ID="0.0.5"\nNAME="AirymaxOS"\n' >"${FX}/distro/release/os-release"
    cat >"${FX}/distro/specs/airymaxos-release.spec" <<'EOF'
%global airy_version 0.0.5
%{_unitdir}/boot-tui.service
%{_unitdir}/airy-session-choice.service
%{_unitdir}/airy-session-switch@.service
%{_unitdir}/lightdm.service.d/10-airymax-session-choice.conf
%{_unitdir}/airymax-desktop-ready.service.d/20-airymax-session-mode.conf
%{buildroot}/usr/lib/airymaxos/desktop/airy-session-cond.sh
%{buildroot}/usr/lib/airymaxos/desktop/airy-session-state
%{buildroot}/usr/lib/airymaxos/desktop/airy-session-cleanup.sh
%{_datadir}/polkit-1/rules.d/50-airy-session-switch.rules
%{_sysconfdir}/airymaxos/system/tui/approvals.d
EOF
    printf '[toolchain]\nchannel = "1.90.0"\n' >"${FX}/system/tui/rust-toolchain.toml"
    printf '[[package]]\nname = "a"\n\n[[package]]\nname = "b"\n' \
        >"${FX}/system/tui/Cargo.lock"
    printf '[source.vendored-sources]\ndirectory = "vendor"\n' \
        >"${FX}/system/tui/.cargo/config.toml"
    : >"${FX}/system/tui/vendor.ledger"
    printf '[Service]\nExecStart=/usr/bin/boot-tui\n' \
        >"${FX}/system/units/boot-tui.service"
    printf '[Service]\nExecStart=/usr/bin/airy-session-pick --timeout 120\n' \
        >"${FX}/system/units/airy-session-choice.service"
    printf '[Service]\nExecStart=/usr/bin/airy-session-mode --switch %%i\n' \
        >"${FX}/system/units/airy-session-switch@.service"
    local g
    for g in no-stub dead-code lang-lock coupling-scan tui-frame \
             ime-corpus input-matrix tui-e2e tui-field; do
        printf '#!/bin/sh\n' >"${FX}/ci/gates/${g}.sh"
    done
    : >"${tmp}/rpm/airymaxos-release-0.0.5.rpm"
    cat >"${tmp}/bin/rpm" <<'EOF'
#!/bin/sh
cat "${RPM_LIST}"
EOF
    chmod +x "${tmp}/bin/rpm"
    rpm_ok
}

# rpm_ok —— §14.16 的完整清单；负例在此之上改一行。
rpm_ok()
{
    cat >"${tmp}/rpm.list" <<'EOF'
-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/bin/boot-tui
-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/bin/airy-session-pick
-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/bin/airy-session-mode
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/lib/systemd/system/boot-tui.service
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/lib/systemd/system/airy-session-choice.service
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/lib/systemd/system/airy-session-switch@.service
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/lib/systemd/system/lightdm.service.d/10-airymax-session-choice.conf
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/lib/systemd/system/airymax-desktop-ready.service.d/20-airymax-session-mode.conf
-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/lib/airymaxos/desktop/airy-session-cond.sh
-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/lib/airymaxos/desktop/airy-session-state
-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/lib/airymaxos/desktop/airy-session-cleanup.sh
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/share/polkit-1/rules.d/50-airy-session-switch.rules
drwxr-xr-x    1 root    root      0 Jan  1 2026 /etc/airymaxos/system/tui/approvals.d
-rw-r--r--    1 root    root    100 Jan  1 2026 /usr/share/airymaxos/system/tui/themes/airy.toml
EOF
    export RPM_LIST="${tmp}/rpm.list"
}

tree
out="$(run --static)"
check "四件套与版本同源判通过" 0 "$?" "${out}" "static 模式"

tree
out="$(run --static --tag v0.0.5-rc1)"
check "rc tag 基线判通过" 0 "$?" "${out}" "static 模式"

tree
printf 'VERSION_ID="0.0.4"\n' >"${FX}/distro/release/os-release"
out="$(run --static)"
check "树内版本不是 0.0.5 判拒绝" 1 "$?" "${out}" "0.0.5 发版要求"

tree
sed -i 's/^%global airy_version 0\.0\.5$/%global airy_version 0.0.4/' \
    "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --static)"
check "spec 与 os-release 不同源判拒绝" 1 "$?" "${out}" "不同源"

tree
out="$(run --static --tag v0.0.4)"
check "tag 基线不同源判拒绝" 1 "$?" "${out}" "tag v0.0.4 的基线"

tree
mkdir -p "${FX}/system/tui/crates/x"
printf '[[package]]\nname = "c"\n' >"${FX}/system/tui/crates/x/Cargo.lock"
out="$(run --static)"
check "第二个 Cargo.lock 判拒绝" 1 "$?" "${out}" "Cargo.lock 数量为 2"

tree
rm -rf "${FX}/system/tui/vendor"
out="$(run --static)"
check "缺 vendor 判拒绝" 1 "$?" "${out}" "缺少 system/tui/vendor"

tree
printf '[toolchain]\nchannel = "1.89.0"\n' >"${FX}/system/tui/rust-toolchain.toml"
out="$(run --static)"
check "工具链与钉版漂移判拒绝" 1 "$?" "${out}" "与钉版 1.90.0 不一致"

tree
rm -f "${FX}/ci/gates/tui-frame.sh"
out="$(run --static)"
check "门禁脚本缺失判拒绝" 1 "$?" "${out}" "缺少 ci/gates/tui-frame.sh"

# 打包规则与 gate 的路径表必须同源：spec 漏登记一条，装机才会缺文件。
tree
sed -i '/20-airymax-session-mode/d' "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --static)"
check "spec 未登记交付路径判拒绝" 1 "$?" "${out}" "未登记交付"

tree
sed -i 's/airy-session-state$/airy-session-stat/' \
    "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --static)"
check "spec 路径拼写漂移判拒绝" 1 "$?" "${out}" "未登记交付"

tree
out="$(run --static --rpm "${tmp}/rpm")"
check "打包清单齐备判通过" 0 "$?" "${out}" "static 模式"

tree
sed -i 's|^-rwxr-xr-x    1 root    root    100 Jan  1 2026 /usr/bin/boot-tui|lrwxrwxrwx    1 root    root     20 Jan  1 2026 /usr/bin/boot-tui|' \
    "${tmp}/rpm.list"
out="$(run --static --rpm "${tmp}/rpm")"
check "入口是软链判拒绝" 1 "$?" "${out}" "不是可执行文件"

tree
sed -i '/^.*\/usr\/bin\/airy-session-pick$/d' "${tmp}/rpm.list"
out="$(run --static --rpm "${tmp}/rpm")"
check "缺会话入口判拒绝" 1 "$?" "${out}" "发行包缺 /usr/bin/airy-session-pick"

tree
sed -i '/boot-tui.service/d' "${tmp}/rpm.list"
out="$(run --static --rpm "${tmp}/rpm")"
check "缺 unit 判拒绝" 1 "$?" "${out}" "发行包缺 /usr/lib/systemd/system/boot-tui.service"

tree
printf '{"components": [{}, {}]}\n' >"${tmp}/sbom.json"
out="$(run --static --sbom "${tmp}/sbom.json")"
check "SBOM 组件数相等判通过" 0 "$?" "${out}" "static 模式"

tree
printf '{"components": [{}, {}, {}]}\n' >"${tmp}/sbom.json"
out="$(run --static --sbom "${tmp}/sbom.json")"
check "SBOM 组件数不等判拒绝" 1 "$?" "${out}" "与 Cargo.lock 条目 2 不等"

tree
out="$(run)"
check "发版口径缺输入退 5" 5 "$?" "${out}" "发版口径缺 --tag"

tree
out="$(run --static --nope)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

if [ "${fails}" -ne 0 ]; then
    printf 'test_release_005: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_release_005: 全部通过\n'
