#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/llm-offline.sh — 离线可用性与首启自检门禁
#
# 用法: ci/gates/llm-offline.sh
#
# 本门禁回答运维在装完机后问的第一个问题：断网时，一条命令能否判定本机
# 模型栈到底能不能用，并把不能用的原因归到具体一层。判定逻辑由单测逐项
# 覆盖，此处钉「对外表现」——退出码、状态字段、逐项结论与被阻塞项。
#
# 声明面：
#
#   * 入口可执行，帮助里能看到 selftest 子命令。
#   * 自检包以单一包形态交付，不与旧的单文件并存。
#   * 自检包不引入网络客户端——「完全断网可用」不能靠运气。
#   * 单元在清单缺失时不启动：没装模型的机器不该反复拉起必败服务，
#     更不该每次开机都往事故单元里记一笔。
#
# 实物面（造一份临时离线包，与 model-manifest 同一套手法）：
#
#   * 就绪或显式降级：8 项全过，退出码 0 或 10；降级必须写
#     degraded:no-accel，判定同时落盘。
#   * 引擎失去可执行位：1 / failed:no-runtime，失败项之前的项保持通过，
#     之后的项逐条记 blocked，而不是从输出里消失。
#   * 资产被等长篡改：1 / failed:digest-mismatch，归因落在 assets。
#   * 空运行根：1 / failed:no-manifest。
#   * 行式输出逐项同形，末行与 status 一样是状态行。
#
# 加速探测结果随机器而变，故就绪与降级不作硬断言：两种都接受，但降级
# 必须给出原因。除这条以外，其余判定与真机显卡无关。
#
# 退出码: 0 通过；1 检查失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

AIRY="${ROOT}/system/bin/airy-model"
PKG="${ROOT}/system/services/airyllm/selftest"
UNIT="${ROOT}/system/units/airy-llm.service"
MANIFEST_GATE=/var/lib/airy-llm/manifest.json

NAMES="root manifest assets engine smoke backend serve status"

TMP=""
HOME_DIR=""

usage()
{
    cat <<'EOF'
用法: ci/gates/llm-offline.sh
EOF
}

# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

trap cleanup EXIT

# 声明面 -------------------------------------------------------------------

check_entry()
{
    chk_entry "${AIRY}"
    "${AIRY}" --help | grep -q 'selftest' ||
        die "命令行帮助里没有 selftest 子命令"
    say "入口可执行且暴露 selftest"
}

check_layout()
{
    local part
    [ -f "${PKG}/__init__.py" ] || die "缺少自检包: ${PKG#"${ROOT}/"}"
    for part in verdict.py steps.py; do
        [ -f "${PKG}/${part}" ] || die "自检包缺少 ${part}"
    done
    [ ! -e "${ROOT}/system/services/airyllm/selftest.py" ] ||
        die "单文件 selftest.py 与自检包并存"
    say "自检实现以单一包形态交付"
}

check_no_net()
{
    local hit
    hit="$(grep -nE '^[[:space:]]*(import|from)[[:space:]]+(socket|ssl|urllib|http|ftplib|smtplib|telnetlib|xmlrpc|requests)' \
        "${PKG}"/*.py || true)"
    [ -z "${hit}" ] || die "自检包引入网络依赖: ${hit}"
    say "自检包不依赖网络客户端"
}

check_unit()
{
    [ -f "${UNIT}" ] || die "缺少单元文件: ${UNIT#"${ROOT}/"}"
    grep -qxF "ConditionPathExists=${MANIFEST_GATE}" "${UNIT}" ||
        die "单元未在清单缺失时跳过启动"
    say "单元在清单缺失时跳过启动"
}

# 实物面 -------------------------------------------------------------------

# shellcheck source=../lib/model-assets.sh
. "${ROOT}/ci/lib/model-assets.sh"

install_bundle()
{
    local out rc

    out="$("${AIRY}" --root "${HOME_DIR}" install --source "${TMP}/bundle")"
    rc=$?
    [ "${rc}" -eq 0 ] || die "install 退出码 ${rc}: ${out}"
}

# 自检判定与契约的比对。失败时要求：首个失败项就是归因项，其后每一项都
# 记成被它阻塞并沿用同一归因，最后一项状态检查必须通过——判定没落盘就
# 等于没判定。
verdict()
{
    python3 - "${NAMES}" "$@" <<'PY'
import json
import sys

names = sys.argv[1].split()
raw, want_state, want_reason, want_fail = sys.argv[2:6]
want_digest = sys.argv[6] if len(sys.argv) > 6 else ""
obj = json.loads(raw)
if sorted(obj) != ["backend", "checks", "digest", "reason", "state"]:
    sys.exit(f"字段集合 {sorted(obj)!r} 偏离契约")
rows = obj["checks"]
got = [row.get("name") for row in rows]
if got != names:
    sys.exit(f"检查项顺序 {got!r} 偏离契约")
for row in rows:
    if sorted(row) != ["detail", "name", "ok", "reason"]:
        sys.exit(f"{row.get('name')}: 元素字段 {sorted(row)!r} 偏离契约")
    if not isinstance(row["ok"], bool):
        sys.exit(f"{row['name']}: ok 必须是布尔值")
if (obj["state"], obj["reason"]) != (want_state, want_reason or None):
    sys.exit(f"state/reason {(obj['state'], obj['reason'])!r}，"
             f"应为 {(want_state, want_reason or None)!r}")
if want_digest and obj["digest"] != want_digest:
    sys.exit(f"清单摘要 {obj['digest']!r}，应为 {want_digest!r}")
bad = [row["name"] for row in rows if not row["ok"]]
if not want_fail:
    if bad:
        sys.exit(f"不应有失败项: {bad!r}")
    sys.exit(0)
if not bad or bad[0] != want_fail:
    sys.exit(f"首个失败项 {bad[:1]!r}，应为 {want_fail!r}")
cause = rows[names.index(want_fail)]
for row in rows[names.index(want_fail) + 1:-1]:
    if row["ok"] or not row["detail"].startswith("blocked by "):
        sys.exit(f"{row['name']}: 应记为被阻塞")
    if row["reason"] != cause["reason"]:
        sys.exit(f"{row['name']}: 阻塞归因 {row['reason']!r} 与失败项不符")
if not rows[-1]["ok"] or rows[-1]["name"] != "status":
    sys.exit("失败判定未落盘")
PY
}

# 行式输出是给人看的那一面：逐项一段，末行与 status 完全同形。
lines_contract()
{
    python3 - "${NAMES}" "$1" <<'PY'
import sys

names = sys.argv[1].split()
rows = sys.argv[2].strip().splitlines()
if len(rows) != len(names) + 1:
    sys.exit(f"行数 {len(rows)}，应为 {len(names) + 1}")
for name, row in zip(names, rows):
    if not row.startswith(f"{name}="):
        sys.exit(f"第 {name} 行偏离契约: {row!r}")
    if row != f"{name}=ok" and not row.startswith(f"{name}=ok "):
        sys.exit(f"{name} 未通过: {row!r}")
if not rows[-1].startswith("state="):
    sys.exit(f"末行不是状态行: {rows[-1]!r}")
PY
}

status_file()
{
    python3 - "$1" "$2" <<'PY'
import json
import sys

home, raw = sys.argv[1:3]
obj = json.loads(raw)
want = {key: obj[key] for key in ("state", "backend", "reason", "digest")}
with open(f"{home}/status.json", encoding="utf-8") as handle:
    kept = json.load(handle)
extra = sorted(set(kept) - {"schema"} - set(want))
if extra:
    sys.exit(f"状态文件含契约外字段: {extra!r}")
for key, value in want.items():
    if kept.get(key) != value:
        sys.exit(f"状态文件 {key}={kept.get(key)!r}，"
                 f"与自检判定 {value!r} 不一致")
PY
}

check_ready()
{
    local out rc digest

    make_bundle
    install_bundle
    out="$("${AIRY}" --root "${HOME_DIR}" selftest --json)"
    rc=$?
    digest="$(sha256sum "${HOME_DIR}/manifest.json" | cut -d' ' -f1)"
    case "${rc}" in
        0)  verdict "${out}" ready "" "" "${digest}" ||
                die "就绪判定不符: ${out}" ;;
        10) verdict "${out}" degraded no-accel "" "${digest}" ||
                die "降级判定不符: ${out}" ;;
        *)  die "自检退出码 ${rc}: ${out}" ;;
    esac
    status_file "${HOME_DIR}" "${out}" || die "判定未按契约落盘"
    say "离线自检收敛到就绪或显式降级"
}

check_lines()
{
    local out rc

    out="$("${AIRY}" --root "${HOME_DIR}" selftest)"
    rc=$?
    case "${rc}" in
        0|10) ;;
        *) die "行式自检退出码 ${rc}: ${out}" ;;
    esac
    lines_contract "${out}" || die "行式输出偏离契约: ${out}"
    say "行式输出逐项同形且以状态行收尾"
}

check_engine()
{
    local engine="${TMP}/engine/llama-server" out rc

    chmod 0644 "${engine}"
    out="$("${AIRY}" --root "${HOME_DIR}" selftest --json)"
    rc=$?
    chmod 0755 "${engine}"
    [ "${rc}" -eq 1 ] || die "引擎不可执行时退出码 ${rc}，应为 1"
    verdict "${out}" failed no-runtime engine ||
        die "未归因到 no-runtime: ${out}"
    say "引擎不可执行按 no-runtime 失败并阻断后续项"
}

check_tamper()
{
    local weight="${HOME_DIR}/models/w.gguf" size out rc

    [ -f "${weight}" ] || die "缺少已落盘资产: ${weight}"
    size="$(wc -c <"${weight}")"
    head -c "${size}" /dev/zero >"${weight}"
    out="$("${AIRY}" --root "${HOME_DIR}" selftest --json)"
    rc=$?
    [ "${rc}" -eq 1 ] || die "篡改后退出码 ${rc}，应为 1"
    verdict "${out}" failed digest-mismatch assets ||
        die "未归因到 digest-mismatch: ${out}"
    say "等长篡改按 digest-mismatch 失败"
}

check_empty()
{
    local out rc

    mkdir -p "${TMP}/empty"
    out="$("${AIRY}" --root "${TMP}/empty" selftest --json)"
    rc=$?
    [ "${rc}" -eq 1 ] || die "空根退出码 ${rc}，应为 1"
    verdict "${out}" failed no-manifest manifest ||
        die "空根未归因到 no-manifest: ${out}"
    say "空根按 no-manifest 失败"
}

main()
{
    TMP="$(mktemp -d)" || die "无法创建临时目录"
    HOME_DIR="${TMP}/home"

    group "离线自检: 声明面"
    check_entry
    check_layout
    check_no_net
    check_unit
    endgroup

    group "离线自检: 实物面"
    check_ready
    check_lines
    check_engine
    check_tamper
    check_empty
    endgroup

    say "== llm-offline passed =="
}

case "${1:-}" in
    '' ) main ;;
    -h|--help) usage ;;
    *) err "未知参数: $1"; usage >&2; exit 1 ;;
esac
