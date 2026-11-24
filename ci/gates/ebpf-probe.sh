#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/ebpf-probe.sh — 可编程探针（P-09）契约与出树构建门禁
#
# 用法:
#   ci/gates/ebpf-probe.sh
#
# eBPF 探针有两条容易静默失效的链路：SEG 名写错时加载成功但永远不触发；
# tracepoint 上下文成员顺序与内核 TP_STRUCT__entry 不一致时，读出来的字段
# 张冠李戴，验证器不会报错。两者都只能在运行期暴露，故把契约固化成静态
# 判据，再补一次真实编译。
#
# 判据：
#   * 目录只放 CT 源码、探测源码与 CMakeLists，不落产物。
#   * kprobe/kretprobe 成对挂 airy_cap_check；tracepoint 挂内核已发布且
#     确有发射点的 airy_policy_decision / airy_superv_enforce。
#   * 探针上下文成员与 include/trace/events/airy.h 的字段顺序逐项相等。
#   * ringbuf 不小于 4 MB、计数用 PERCPU_ARRAY、map 均显式 max_entries。
#   * 不含 pt_regs / PT_REGS：字节码因此与架构无关，四架构共用。
#   * 加载器显式销毁 link、释放 ringbuf、关闭 object，日志落
#     /var/log/airymaxos/bpf-load.log。
#   * 以 -g -O2 -target bpf 出树构建，产物小于 64 KB 且带 .BTF 段。
#
# 退出码: 0 通过；1 契约或构建不成立；5 参数或目录不满足。
# ===========================================================================
# OBS_* 配置变量由 ci/lib/obs.sh 读取，shellcheck 无法跨文件跟踪，
# 故本文件整体关闭「变量未使用」。
# shellcheck disable=SC2034
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/apt.sh
. "${ROOT}/ci/lib/apt.sh"
# shellcheck source=../lib/obs.sh
. "${ROOT}/ci/lib/obs.sh"

SRC="${ROOT}/kernel"
EBPF="${ROOT}/system/obs/ebpf"
BPF_C="${EBPF}/airy_probe.bpf.c"
BPF_L="${EBPF}/airy_probe.c"
CMAKE="${EBPF}/CMakeLists.txt"

need_dir --src "${SRC}"
need_file "${BPF_C}" "探针源码"
need_file "${BPF_L}" "加载器源码"
need_file "${CMAKE}" "构建脚本"

# 判据只看代码：文件头会用文字解释为什么不读寄存器上下文，注释里的这个词
# 不代表代码真的读了。
code_only()
{
    grep -vE '^[[:space:]]*(/\*|\*)' "$1"
}

# 目录整洁与头注释 ---------------------------------------------------------
chk_files()
{
    local f

    group "目录与头注释"
    obs_dir "${EBPF}" 'CMakeLists.txt airy_probe.bpf.c airy_probe.c '
    for f in "${BPF_C}" "${BPF_L}"; do
        obs_head "${f}" "$(basename "${f}")"
    done
    have 'SPDX-License-Identifier' "${CMAKE}" "CMakeLists 缺少 SPDX"
    deny 'pt_regs|PT_REGS' <(code_only "${BPF_C}") \
        "探针读取 pt_regs（架构相关偏移）"
    deny 'pt_regs|PT_REGS' <(code_only "${BPF_L}") "加载器引用 pt_regs"
    endgroup
}

# 挂载面 -------------------------------------------------------------------
chk_hook()
{
    local cap="${SRC}/security/airy/airy_cap_check.c"

    group "挂载面"
    have 'SEC\("kprobe/airy_cap_check"\)' "${BPF_C}" "缺少能力判定入口探针"
    have 'SEC\("kretprobe/airy_cap_check"\)' "${BPF_C}" "缺少能力判定返回探针"
    have 'SEC\("tracepoint/airy/airy_policy_decision"\)' "${BPF_C}" \
        "缺少策略裁决探针"
    have 'SEC\("tracepoint/airy/airy_superv_enforce"\)' "${BPF_C}" \
        "缺少监督强制探针"
    have '^int airy_cap_check\(' "${cap}" "内核缺少 airy_cap_check 定义"
    deny 'notrace|__always_inline' "${cap}" \
        "airy_cap_check 不可被 kprobe 挂载"
    have 'trace_airy_policy_decision\(' "${SRC}/security/airy/airy_policy.c" \
        "airy_policy_decision 没有发射点"
    have 'trace_airy_superv_enforce\(' \
        "${SRC}/security/airy/airy_die_notify.c" \
        "airy_superv_enforce 没有发射点"
    endgroup
}

# tracepoint 上下文契约 ----------------------------------------------------
evt_fields()
{
    awk -v ev="TRACE_EVENT(${1}," 'index($0, ev) { f = 1 }
        f && /TP_STRUCT__entry\(/ { g = 1; next }
        g && /^\t\),/ { exit }
        g' "${SRC}/include/trace/events/airy.h" |
        sed -n 's/.*__field([[:space:]]*u32,[[:space:]]*\([a-z0-9_]*\).*/\1/p'
}

ctx_fields()
{
    sed -n "/^struct ${1} {/,/^};/p" "${BPF_C}" |
        sed -n 's/^[[:space:]]*__u32[[:space:]]*\([a-z0-9_]*\);.*/\1/p'
}

chk_ctx()
{
    local ev ctx k b

    group "上下文契约"
    for pair in "airy_policy_decision:pd_ctx" "airy_superv_enforce:enf_ctx"; do
        ev="${pair%%:*}"
        ctx="${pair##*:}"
        k="$(evt_fields "${ev}")"
        b="$(ctx_fields "${ctx}")"
        if [ -z "${k}" ]; then
            bad "${ev}: 内核头未解析出字段"
        elif [ "${k}" != "${b}" ]; then
            bad "${ctx}: 字段顺序与 ${ev} 不一致"
            diff <(printf '%s\n' "${k}") <(printf '%s\n' "${b}") >&2 || true
        fi
    done
    endgroup
}

# 事件通道与计数 -----------------------------------------------------------
chk_maps()
{
    group "事件通道与计数"
    have 'BPF_MAP_TYPE_RINGBUF' "${BPF_C}" "事件通道不是 ringbuf"
    have 'max_entries, 1U << 22' "${BPF_C}" "ringbuf 不足 4 MB（OS-OBS-020）"
    have 'BPF_MAP_TYPE_PERCPU_ARRAY' "${BPF_C}" "计数不是 per-CPU array"
    have 'max_entries, ST_MAX' "${BPF_C}" "计数 map 未显式 max_entries"
    have 'bpf_ringbuf_reserve\(' "${BPF_C}" "事件未预留即写"
    have 'bpf_ringbuf_submit\(' "${BPF_C}" "事件未提交"
    have '_license\[\] SEC\("license"\) = "GPL"' "${BPF_C}" \
        "缺少 GPL 许可证声明"
    endgroup
}

# 加载器资源路径 -----------------------------------------------------------
chk_loader()
{
    group "加载器资源路径"
    have 'bpf_link__destroy\(' "${BPF_L}" "未显式销毁 link（OS-OBS-018）"
    have 'ring_buffer__free\(' "${BPF_L}" "未释放 ringbuf"
    have 'bpf_object__close\(' "${BPF_L}" "未关闭 bpf_object"
    have 'libbpf_set_print\(' "${BPF_L}" "未接管验证器日志"
    have '/var/log/airymaxos/bpf-load\.log' "${BPF_L}" "日志路径不符"
    have 'DESTINATION lib/airymaxos/bpf' "${CMAKE}" \
        "字节码未安装到 lib/airymaxos/bpf（OS-STD-OBS-014）"
    have 'DESTINATION bin' "${CMAKE}" "加载器未安装到 bin"
    deny 'add_subdirectory\(obs' "${ROOT}/system/CMakeLists.txt" \
        "system 顶层工程引入了 eBPF 依赖"
    endgroup
}

# 编译参数与产物 -----------------------------------------------------------
chk_flags()
{
    group "编译参数"
    have '\-g \-O2 \-target bpf' "${CMAKE}" "编译参数不符 OS-OBS-015"
    endgroup
}

# chk_obj DIR —— 字节码体积与 BTF 段两处产物判据。
chk_obj()
{
    local obj="$1/b/airy_probe.bpf.o" size

    if [ ! -f "${obj}" ]; then
        bad "缺少 airy_probe.bpf.o"
        return
    fi
    size="$(stat -c %s "${obj}")"
    if [ "${size}" -ge 65536 ]; then
        bad "字节码 ${size} 字节，超过 64 KB（OS-KER-115）"
    fi
    if ! readelf -S "${obj}" 2>/dev/null | grep -qF '.BTF'; then
        bad "字节码缺少 .BTF 段"
    fi
}

bpf_ok()
{
    pkg-config --exists libbpf
}

OBS_TITLE="可编程探针"
OBS_LABEL="eBPF "
OBS_BIN="airy-probe"
OBS_CMDS=(clang cmake)
OBS_PKGS=(clang libbpf-dev pkg-config)
OBS_PROBE=bpf_ok

SRC_DIR="${EBPF}"
chk_out()
{
    chk_obj "$1"
}

obs_gate chk_files chk_hook chk_ctx chk_maps chk_loader chk_flags
