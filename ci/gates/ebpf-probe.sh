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
#   * 四个内核边界（airy_cap_check、airy_clt_serve、airy_sched_ctl、
#     airy_invoke_dispatch）各有一对 kprobe/kretprobe；tracepoint 挂内核
#     已发布且确有发射点的 airy_policy_decision / airy_superv_enforce。
#   * 四个符号以非 static 定义出现，且不带 notrace / __always_inline。
#   * 四架构 defconfig 齐备 eBPF 装载与 kprobe 所需配置项。
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
# probe_ok SYM FILE —— 符号须以非 static、无 notrace/__always_inline 的定义
# 出现，否则 kprobe 要么找不到符号，要么在内联后失去挂载点。
probe_ok()
{
    local sym="$1" file="$2" ctx

    ctx="$(code_only "${file}" |
        grep -B3 -E "^(__must_check )?int ${sym}\\(" || true)"
    if [ -z "${ctx}" ]; then
        bad "${sym}: 未以非 static 符号定义"
        return
    fi
    if printf '%s\n' "${ctx}" | grep -qE 'notrace|__always_inline'; then
        bad "${sym}: 带 notrace/__always_inline，不可挂 kprobe"
    fi
}

chk_hook()
{
    local d="${SRC}/security/airy" s="${SRC}/kernel/syscalls"

    group "挂载面"
    have 'SEC\("kprobe/" #sym\)' "${BPF_C}" "探针未成对生成入口挂载"
    have 'SEC\("kretprobe/" #sym\)' "${BPF_C}" "探针未成对生成返回挂载"
    for sym in airy_cap_check airy_clt_serve airy_sched_ctl \
               airy_invoke_dispatch; do
        have "AIRY_TRACE_PAIR\\(${sym}," "${BPF_C}" \
            "缺少 ${sym} 的 kprobe/kretprobe 对"
    done
    have 'SEC\("tracepoint/airy/airy_policy_decision"\)' "${BPF_C}" \
        "缺少策略裁决探针"
    have 'SEC\("tracepoint/airy/airy_superv_enforce"\)' "${BPF_C}" \
        "缺少监督强制探针"
    probe_ok airy_cap_check "${d}/airy_cap_check.c"
    probe_ok airy_clt_serve "${s}/airy_clt.c"
    probe_ok airy_sched_ctl "${s}/airy_sched.c"
    probe_ok airy_invoke_dispatch "${s}/airy_syscalls.c"
    have 'trace_airy_policy_decision\(' "${d}/airy_policy.c" \
        "airy_policy_decision 没有发射点"
    have 'trace_airy_superv_enforce\(' "${d}/airy_die_notify.c" \
        "airy_superv_enforce 没有发射点"
    endgroup
}

# 四架构配置 ---------------------------------------------------------------
chk_conf()
{
    local arch cfg k

    group "四架构配置"
    for arch in x86 arm64 riscv loongarch; do
        cfg="${SRC}/arch/${arch}/configs/airy_defconfig"
        if [ ! -f "${cfg}" ]; then
            bad "${arch}: 缺少 defconfig"
            continue
        fi
        for k in BPF BPF_SYSCALL BPF_JIT BPF_JIT_ALWAYS_ON BPF_EVENTS \
                 BPF_UNPRIV_DEFAULT_OFF DEBUG_INFO_BTF KPROBES; do
            have "^CONFIG_${k}=y$" "${cfg}" "${arch} 未开启 CONFIG_${k}"
        done
    done
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

obs_gate chk_files chk_hook chk_conf chk_ctx chk_maps chk_loader chk_flags
