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
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/apt.sh
. "${ROOT}/ci/lib/apt.sh"

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
# chk_head FILE —— 源文件首行 SPDX 标识与首六行版权行。
chk_head()
{
    local f="$1"

    [ "$(sed -n '1p' "${f}")" = '// SPDX-License-Identifier: GPL-2.0-only' ] ||
        bad "$(basename "${f}"): 首行缺少 SPDX"
    sed -n '1,6p' "${f}" | grep -q 'Copyright' ||
        bad "$(basename "${f}"): 缺少版权行"
}

chk_files()
{
    local f got

    group "目录与头注释"
    got="$(find "${EBPF}" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort |
        tr '\n' ' ')"
    if [ "${got}" != 'CMakeLists.txt airy_probe.bpf.c airy_probe.c ' ]; then
        bad "目录内容不符: ${got}"
    fi
    for f in "${BPF_C}" "${BPF_L}"; do
        chk_head "${f}"
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

# 出树构建 -----------------------------------------------------------------
# deps —— 探针构建依赖；已就位直接返回，否则一次装上。
deps()
{
    if command -v clang >/dev/null 2>&1 && command -v cmake >/dev/null 2>&1 &&
        pkg-config --exists libbpf; then
        return 0
    fi
    note "安装 eBPF 构建依赖"
    apt_sync && apt_install_min clang libbpf-dev pkg-config
}

# build_tree DIR —— 在 DIR 内配置并编译字节码；失败点各自留一行日志尾巴。
build_tree()
{
    local dir="$1" log="$1/build.log"

    if ! deps; then
        bad "eBPF 构建依赖不可用"
        return 1
    fi
    if ! cmake -S "${EBPF}" -B "${dir}/b" -DCMAKE_BUILD_TYPE=Release \
        >"${log}" 2>&1; then
        bad "cmake 配置失败"
        tail -n 20 "${log}" >&2
        return 1
    fi
    if ! cmake --build "${dir}/b" -j "$(nproc)" >>"${log}" 2>&1; then
        bad "编译失败"
        tail -n 40 "${log}" >&2
        return 1
    fi
}

# chk_obj DIR —— 字节码体积、BTF 段与加载器三处产物判据。
chk_obj()
{
    local dir="$1" obj="$1/b/airy_probe.bpf.o" size

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
    need_exe "${dir}/b/airy-probe" "airy-probe 加载器"
}

chk_build()
{
    local tmp

    group "出树构建"
    have '\-g \-O2 \-target bpf' "${CMAKE}" "编译参数不符 OS-OBS-015"
    tmp="$(mktemp -d)"
    if [ -z "${tmp}" ]; then
        bad "临时目录创建失败"
        endgroup
        return
    fi
    if build_tree "${tmp}"; then
        chk_obj "${tmp}"
    fi
    rm -rf "${tmp}"
    endgroup
}

chk_files
chk_hook
chk_ctx
chk_maps
chk_loader
chk_build

if [ "${FAILS}" -ne 0 ]; then
    err "可编程探针门禁失败 ${FAILS} 项"
    exit 1
fi

say "可编程探针门禁通过"
exit 0
