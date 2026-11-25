#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/release-005.sh — 0.0.5 发版自证
#
# 用法:
#   ci/gates/release-005.sh [--root DIR] [--tag TAG] [--rpm DIR]
#                            [--sbom FILE] [--evidence DIR] [--out DIR]
#   ci/gates/release-005.sh --static [...]
#
# 发版自证回答的不是「功能写完了吗」，而是「这一版能不能挂发行 tag」。
# 它只消费已经存在的门禁与产物，不新增任何检查：0.0.4 的一处真实事故正是
# 发版脚本与 CI 判据分叉，同一版在两侧得到不同结论。因此本 gate 的全部
# 判据都来自 §9 的 QG 与 §15 的 AC，逐条对应、不复写。
#
# 判据四组：
#   版本一致性：distro/release/os-release 的 VERSION_ID 与 release.spec 的
#     airy_version 同源，且基线为 0.0.5；给 --tag 时 tag 去前缀、去 -rcN 后
#     必须同一基线。两侧各说各话时，产物名与包内版本会一直漂到装机才暴露。
#   供应链四件套：工具链钉版与 rust-toolchain.toml 一致、工作区唯一
#     Cargo.lock、vendor/ 与 .cargo/config.toml 齐备、给 --sbom 时组件数与
#     Cargo.lock 条目相等。
#   打包清单：给 --rpm 时用 rpm -qlpv 逐条断言 §14.16 的目标路径。入口二进制
#     不在脚本里另抄一份文件名表：清单从 system/units 的 ExecStart 推导，
#     单元引用的 /usr/bin 路径缺一个就是开机降级（0.0.5 的 session-choice
#     以 203/EXEC 失败即此）。入口必须是真实二进制（-rwx），不能是软链——
#     软链会让 %{_bindir} 出现「同一条目两种形态」，%check 的 test -L 断言
#     随之误判。systemctl 由 systemd 提供，不在发行包内，推导时排除。
#   门禁全集：默认（发版口径）按 §10「发版只跑既有门禁的全集并归档输出」
#     逐个执行 ci/ci.sh，日志落 --out；tui-field 用 --evidence 指向的实机
#     读数，夹具不计入。--static 只核对仓库事实，明确不构成发版结论。
#
# 退出码: 0 通过；1 任一判据不成立；5 参数不满足（发版口径缺 --tag/--rpm/
# --evidence 时即退 5，不把「少给了输入」降级成「通过」）。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

readonly BASE=0.0.5
readonly GATES=(no-stub dead-code lang-lock coupling-scan tui-frame
                ime-corpus input-matrix tui-e2e)
readonly UNITS=(/usr/lib/systemd/system/boot-tui.service
                /usr/lib/systemd/system/airy-session-choice.service
                /usr/lib/systemd/system/airy-session-switch@.service
                /usr/lib/systemd/system/lightdm.service.d/10-airymax-session-choice.conf
                /usr/lib/systemd/system/airymax-desktop-ready.service.d/10-airymax-session-choice.conf)
readonly FILES=(/usr/lib/airymaxos/desktop/airy-session-cond.sh
                /usr/lib/airymaxos/desktop/airy-session-state
                /usr/lib/airymaxos/desktop/airy-session-cleanup.sh
                /usr/share/polkit-1/rules.d/50-airy-session-switch.rules
                /etc/airymaxos/system/tui/approvals.d)

ROOT=""
TAG=""
RPMDIR=""
SBOM=""
FIELD=""
OUT="${TMPDIR:-/tmp}/airy-release-005"
STATIC=0
BAD=0

usage()
{
    cat <<'EOF'
用法: ci/gates/release-005.sh [--root DIR] [--tag TAG] [--rpm DIR]
                              [--sbom FILE] [--evidence DIR] [--out DIR]
      ci/gates/release-005.sh --static [...]
EOF
}

fail()
{
    err "$*"
    BAD=$(( BAD + 1 ))
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
        --tag) TAG="${2:-}"; [ -n "${TAG}" ] || { err "--tag 需要取值"; exit 5; }; shift 2 ;;
        --rpm) need_dir "$1" "${2:-}"; RPMDIR="$2"; shift 2 ;;
        --sbom) [ -f "${2:-}" ] || { err "--sbom 文件不存在: ${2:-}"; exit 5; }
                SBOM="$2"; shift 2 ;;
        --evidence) need_dir "$1" "${2:-}"; FIELD="$2"; shift 2 ;;
        --out) OUT="${2:-}"; [ -n "${OUT}" ] || { err "--out 需要取值"; exit 5; }; shift 2 ;;
        --static) STATIC=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit 5 ;;
    esac
done

[ -n "${ROOT}" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TUIDIR="${ROOT}/system/tui"
mkdir -p "${OUT}" || { err "无法建立输出目录: ${OUT}"; exit 5; }

if [ "${STATIC}" -eq 0 ]; then
    for pair in "tag:${TAG}" "rpm:${RPMDIR}" "evidence:${FIELD}"; do
        [ -n "${pair#*:}" ] || { err "发版口径缺 --${pair%%:*}"
                                 exit 5; }
    done
fi

# ver_tree —— 树内版本声明的单一来源；缺声明时返回空，由调用方判负。
ver_tree()
{
    sed -n 's/^VERSION_ID="\([^"]*\)".*/\1/p' \
        "${ROOT}/distro/release/os-release"
}

# ver_spec —— release.spec 的版本宏；spec 缺失同样返回空。
ver_spec()
{
    sed -n 's/^%global[[:space:]]\{1,\}airy_version[[:space:]]\{1,\}//p' \
        "${ROOT}/distro/specs/airymaxos-release.spec"
}

ver_check()
{
    local tree spec

    group "版本一致性"
    tree="$(ver_tree)"
    spec="$(ver_spec)"
    if [ "${tree}" != "${BASE}" ]; then
        fail "os-release VERSION_ID=${tree:-缺失}，0.0.5 发版要求 ${BASE}"
    fi
    if [ "${spec}" != "${tree}" ]; then
        fail "release.spec airy_version=${spec:-缺失} 与 VERSION_ID=${tree:-缺失} 不同源"
    fi
    if [ -n "${TAG}" ]; then
        local base="${TAG#v}"
        base="${base%%-*}"
        [ "${base}" = "${tree}" ] || fail "tag ${TAG} 的基线 ${base} 与树内版本 ${tree} 不同源"
    fi
    endgroup
}

# pin_of —— 工具链钉版只从 ci/lib/tui_gate.sh 读，避免第三份副本。
pin_of()
{
    sed -n 's/^readonly[[:space:]]\{1,\}PIN=//p' "${ROOT}/ci/lib/tui_gate.sh"
}

# ch_of —— rust-toolchain.toml 的 channel 取值。
ch_of()
{
    sed -n 's/^channel[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
        "${TUIDIR}/rust-toolchain.toml" 2>/dev/null
}

lock_check()
{
    local n

    n="$(find "${TUIDIR}" -name Cargo.lock -not -path '*/vendor/*' \
         -not -path '*/target/*' | wc -l)"
    [ "${n}" -eq 1 ] || fail "工作区 Cargo.lock 数量为 ${n}，应为 1"
}

vend_check()
{
    [ -f "${TUIDIR}/.cargo/config.toml" ] || fail "缺少 system/tui/.cargo/config.toml"
    [ -d "${TUIDIR}/vendor" ] || fail "缺少 system/tui/vendor"
    [ -f "${TUIDIR}/vendor.ledger" ] || fail "缺少 system/tui/vendor.ledger"
}

tool_check()
{
    local pin ch

    pin="$(pin_of)"
    ch="$(ch_of)"
    [ -n "${pin}" ] || { fail "ci/lib/tui_gate.sh 未声明 PIN"; return 0; }
    [ "${ch}" = "${pin}" ] ||
        fail "rust-toolchain.toml channel=${ch:-缺失} 与钉版 ${pin} 不一致"
}

# sbom_check —— 组件数必须与 Cargo.lock 条目相等（AC-S3）。
sbom_check()
{
    local n lock_num

    [ -n "${SBOM}" ] || return 0
    lock_num="$(grep -c '^\[\[package\]\]' "${TUIDIR}/Cargo.lock")"
    n="$(python3 - "${SBOM}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    doc = json.load(fh)
items = doc.get("components") or doc.get("packages") or []
print(len(items))
PY
)" || { fail "SBOM 无法解析: ${SBOM}"; return 0; }
    [ "${n}" = "${lock_num}" ] ||
        fail "SBOM 组件数 ${n} 与 Cargo.lock 条目 ${lock_num} 不等"
}

supply_check()
{
    group "供应链四件套"
    tool_check
    lock_check
    vend_check
    sbom_check
    endgroup
}

# unit_bins —— 入口二进制清单从单元推导，不另抄文件名表。单元 Exec* 的首
# token 落在 /usr/bin 的，发行包就必须交付该路径：0.0.5 的 session-choice
# 以 203/EXEC 降级，正是单元先于二进制进镜像。systemctl 由 systemd 提供，
# 不在发行包内，推导时排除。取不到任何条目说明单元树缺失，判负而不是跳过。
unit_bins()
{
    local dir="${ROOT}/system/units" unit

    [ -d "${dir}" ] || return 0
    while IFS= read -r unit; do
        sed -n -E \
            's/^[[:space:]]*Exec[A-Za-z]*[[:space:]]*=[[:space:]]*[-@+!:]*(.*)$/\1/p' \
            "${unit}"
    done < <(find "${dir}" -maxdepth 1 -type f -name '*.service' | sort) |
        awk 'NF { print $1 }' |
        awk '$0 ~ /^\/usr\/bin\// && $0 != "/usr/bin/systemctl"' | sort -u
}

# rpm_list —— 合并发行包与系统包的文件清单，供逐条断言。
rpm_list()
{
    local f
    for f in "${RPMDIR}"/airymaxos-release-*.rpm "${RPMDIR}"/airymaxos-system-*.rpm; do
        [ -f "${f}" ] || continue
        rpm -qlpv "${f}" 2>/dev/null
    done
}

# rpm_find LIST PATH —— 输出该路径的权限串；不存在时输出空。
rpm_find()
{
    awk -v p="$2" '$NF == p { print $1; exit }' <<<"$1"
}

rpm_check()
{
    local list p mode themes bins

    [ -n "${RPMDIR}" ] || return 0
    need_cmd rpm
    group "打包清单"
    list="$(rpm_list)"
    [ -n "${list}" ] || { fail "${RPMDIR} 内无发行包（airymaxos-release/system）"; endgroup; return 0; }

    bins="$(unit_bins)"
    if [ -z "${bins}" ]; then
        fail "无法从 system/units 推导入口二进制"
    else
        while IFS= read -r p; do
            mode="$(rpm_find "${list}" "${p}")"
            case "${mode}" in
                '') fail "发行包缺 ${p}" ;;
                -rwx*) ;;
                *) fail "${p} 不是可执行文件（${mode}）" ;;
            esac
        done <<<"${bins}"
    fi
    for p in "${UNITS[@]}" "${FILES[@]}"; do
        [ -n "$(rpm_find "${list}" "${p}")" ] || fail "发行包缺 ${p}"
    done
    themes="$(awk '$NF ~ /^\/usr\/share\/airymaxos\/system\/tui\/themes\/.+\.toml$/ { n++ } END { print n + 0 }' <<<"${list}")"
    [ "${themes}" -gt 0 ] || fail "发行包缺 /usr/share/airymaxos/system/tui/themes/*.toml"
    endgroup
}

gate_need()
{
    local g

    group "门禁脚本登记"
    for g in "${GATES[@]}" tui-field; do
        [ -f "${ROOT}/ci/gates/${g}.sh" ] || fail "缺少 ci/gates/${g}.sh"
    done
    endgroup
}

gate_run()
{
    local g rc

    for g in "${GATES[@]}" tui-field; do
        group "发版门禁 ${g}"
        rc=0
        if [ "${g}" = tui-field ]; then
            bash "${ROOT}/ci/ci.sh" tui-field --input "${FIELD}" \
                >"${OUT}/${g}.log" 2>&1 || rc=$?
        else
            bash "${ROOT}/ci/ci.sh" "${g}" \
                >"${OUT}/${g}.log" 2>&1 || rc=$?
        fi
        if [ "${rc}" -eq 0 ]; then
            say "通过（日志 ${OUT}/${g}.log）"
        else
            err "失败 rc=${rc}（日志 ${OUT}/${g}.log）"
            BAD=$(( BAD + 1 ))
        fi
        endgroup
    done
}

ver_check
supply_check
gate_need
rpm_check

if [ "${STATIC}" -eq 0 ]; then
    gate_run
else
    warn "static 模式：只核对仓库事实，不构成发版结论"
fi

{
    printf 'ver=%s\n' "$(ver_tree)"
    printf 'tag=%s\n' "${TAG:-none}"
    printf 'mode=%s\n' "$([ "${STATIC}" -eq 1 ] && echo static || echo release)"
    printf 'result=%s\n' "$([ "${BAD}" -eq 0 ] && echo pass || echo fail)"
    printf 'out=%s\n' "${OUT}"
} >"${OUT}/release-005-summary.txt"

[ "${BAD}" -eq 0 ] || die "发版自证有 ${BAD} 项不成立（汇总 ${OUT}/release-005-summary.txt）"
say "发版自证通过（0.0.5，汇总 ${OUT}/release-005-summary.txt）"
