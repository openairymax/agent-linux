#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_llm_accel.sh — llm-accel 门禁回归夹具
#
# 门禁判两件事：探测入口以单一包形态交付，且行式与 JSON 两种渲染同源。
# 夹具在临时仓库里放一个探针替身，让门禁跑真实契约模块（accel_contract.py
# 从仓库复制），逐条破坏：段序、字段集合、两种渲染的自洽、只读性、入口
# 形态与包布局。断言只看退出码与稳定判据字符串。
#
# 替身按环境变量 PROBE_VARIANT 切换缺陷形态，而不是每次改脚本源码：变异
# 集中在同一份替身里，用例只声明「要哪一种破坏」，读起来就是缺陷清单。
#
# 用法: bash ci/tests/test_llm_accel.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
BASE="${tmp}/base"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

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

# probe FILE — 探针替身；PROBE_VARIANT 为空时完全合规
probe()
{
    cat >"$1" <<'EOF'
#!/usr/bin/env python3
import json
import os
import sys

V = os.environ.get("PROBE_VARIANT", "")
ROWS = [("cuda", False, ["missing:no /dev/nvidia0"]),
        ("rocm", False, ["missing:no /dev/kfd"]),
        ("vulkan", False, ["missing:no ICD"]),
        ("cpu", True, ["builtin:cpu"])]

if V == "bad-order":
    ROWS[0], ROWS[1] = ROWS[1], ROWS[0]


def rows():
    out = [{"backend": n, "present": p, "evidence": e}
           for n, p, e in ROWS]
    if V == "extra-field":
        out = [dict(r, vendor="x") for r in out]
    return out


def main():
    args = sys.argv[1:]
    if V == "fail":
        sys.exit(1)
    if V == "leak" and "--root" in args:
        root = args[args.index("--root") + 1]
        open(os.path.join(root, "leak"), "w").close()
    if "--json" in args:
        print(json.dumps({"backends": rows()}))
    elif V == "disagree":
        print("cuda=absent rocm=absent vulkan=present cpu=present")
    else:
        print(" ".join("%s=%s" % (r["backend"],
                                  "present" if r["present"] else "absent")
                       for r in rows()))


main()
EOF
    chmod +x "$1"
}

mkdir -p "${BASE}/ci/gates" "${BASE}/ci/lib" "${BASE}/system/bin" \
    "${BASE}/system/services/airyllm/accel"
cp "${ROOT}/ci/gates/llm-accel.sh" "${BASE}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${BASE}/ci/lib/"
cp "${ROOT}/ci/lib/accel_contract.py" "${BASE}/ci/lib/"
probe "${BASE}/system/bin/airy-model"
for part in __init__ fs device icd probe; do
    printf '# SPDX-License-Identifier: Apache-2.0\n' \
        >"${BASE}/system/services/airyllm/accel/${part}.py"
done

# tree NAME — 复制合规树，打印路径
tree()
{
    cp -a "${BASE}" "${tmp}/$1"
    printf '%s\n' "${tmp}/$1"
}

# runin VARIANT TREE [ARGS...] — 以指定缺陷形态在树里跑门禁
runin()
{
    local variant="$1" t="$2"

    shift 2
    PROBE_VARIANT="${variant}" GITHUB_ACTIONS='' \
        bash "${t}/ci/gates/llm-accel.sh" "$@" 2>&1
}

out="$(runin '' "${BASE}" -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

# 正向：合规探针与单一包形态
out="$(runin '' "${BASE}")"; rc=$?
check "合规探针通过" 0 "${rc}" "${out}" "llm-accel passed"

# 负向：声明面
t="$(tree not-exec)"
chmod -x "${t}/system/bin/airy-model"
out="$(runin '' "${t}")"; rc=$?
check "入口不可执行" 1 "${rc}" "${out}" "入口不可执行"

t="$(tree bad-shebang)"
sed -i '1s|.*|#!/bin/sh|' "${t}/system/bin/airy-model"
out="$(runin '' "${t}")"; rc=$?
check "入口首行非 python3" 1 "${rc}" "${out}" "入口首行不是 python3 shebang"

t="$(tree no-part)"
rm -f "${t}/system/services/airyllm/accel/probe.py"
out="$(runin '' "${t}")"; rc=$?
check "探测包缺模块" 1 "${rc}" "${out}" "探测包缺少 probe.py"

t="$(tree stale-file)"
printf '# old\n' >"${t}/system/services/airyllm/accel.py"
out="$(runin '' "${t}")"; rc=$?
check "单文件与探测包并存" 1 "${rc}" "${out}" "单文件 accel.py 与探测包并存"

# 负向：输出面
out="$(runin bad-order "${BASE}")"; rc=$?
check "后端顺序偏离契约" 1 "${rc}" "${out}" "行式输出偏离契约"

out="$(runin extra-field "${BASE}")"; rc=$?
check "JSON 元素字段漂移" 1 "${rc}" "${out}" "JSON 输出偏离契约"

out="$(runin disagree "${BASE}")"; rc=$?
check "两种渲染不自洽" 1 "${rc}" "${out}" "行式与 JSON 输出不一致"

out="$(runin leak "${BASE}")"; rc=$?
check "探针写入运行根" 1 "${rc}" "${out}" "probe 写入了运行时根"

out="$(runin fail "${BASE}")"; rc=$?
check "probe 退出码非 0" 1 "${rc}" "${out}" "probe 行式输出退出码非 0"

if [ "${fails}" -ne 0 ]; then
    printf 'test_llm_accel: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_llm_accel: 全部通过\n'
