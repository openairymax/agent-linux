#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/memory-ops.sh — 记忆运维（P-17 / CA-054）的布局、块格式、碎片
# 整理、迁移、擦除审计与打包接线门禁
#
# 用法:
#   ci/gates/memory-ops.sh
#
# 断言链：
#   1. 运维包、入口与七份测试齐全，源码带 SPDX 头。
#   2. 全部模块可编译，单元测试全跑通过。
#   3. 失败路径可归因：参数错 2、状态或存储不允许 3、权限拒绝 4。
#   4. 数据块魔数与版本可往返，索引与实际项一致。
#   5. defrag 前后碎片率下降且记忆项不丢。
#   6. 克隆保留源、移动移除源、敏感项与跨属主边界成立。
#   7. 覆写擦除留下审计与字节证据，crypto 显式拒绝并退出 3。
#   8. 配置项可覆盖默认块大小，坏配置退出 3。
#   9. CMake、spec、workflow 接线成立，包内无桩标记与本机路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

SYS="${ROOT}/system"
PKG="${SYS}/ops/memory"
ENTRY="${SYS}/bin/airy-memory"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/agent-verify.yml"

need_cmd python3
export PYTHONDONTWRITEBYTECODE=1

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
MEM="${WORK}/mem"

mem() {
    python3 "${ENTRY}" --root "${MEM}" --conf "${WORK}/none.conf" \
        --log-dir "${WORK}/logs" "$@"
}

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "memory-ops: 布局"
for name in __init__.py __main__.py model.py store.py stats.py defrag.py \
    transfer.py erase.py audit.py config.py parser.py cli.py; do
    [ -f "${PKG}/${name}" ] || bad "缺少模块 ${name}"
done
for part in model store stats defrag transfer erase cli; do
    [ -f "${SYS}/tests/ops/test_memory_${part}.py" ] \
        || bad "缺少测试 test_memory_${part}.py"
done
[ -x "${ENTRY}" ] || bad "入口不可执行: bin/airy-memory"
for src in "${PKG}"/*.py "${ENTRY}"; do
    have '^# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0' \
        "${src}" "缺少 SPDX 标识: ${src}"
done
endgroup

# ─── 2. 编译与单元测试 ───────────────────────────────────────────────
group "memory-ops: 编译与单元测试"
for src in "${PKG}"/*.py; do
    python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' \
        "${src}" || bad "无法编译: ${src}"
done
if ! (cd "${SYS}" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
    -s tests/ops -p 'test_memory_*.py'); then
    bad "记忆运维单元测试未通过"
fi
endgroup

# ─── 3. 数据面场景 ───────────────────────────────────────────────────
group "memory-ops: 数据面"
mkdir -p "${WORK}/logs"
python3 - "${WORK}" "${SYS}" <<'PY' || bad "数据面断言失败"
import json
import os
import sys
from pathlib import Path

work = Path(sys.argv[1])
sys.path[:0] = [sys.argv[2] + "/ops", sys.argv[2] + "/services"]

from memory import audit, config, defrag, erase, stats, transfer
from memory.model import (BadState, BadUnsupported, Item, Record,
                          decode_block, encode_block)
from memory.store import Store

os.environ["AIRY_MEMORY_LOGDIR"] = str(work / "logs")
os.environ["AIRY_MEMORY_OPERATOR"] = "gate"
cfg = config.load(str(work / "none.conf"))
st = Store(str(work / "mem"), 4)

rows = [Record(Item("fmt", b"payload", True))]
assert decode_block(encode_block(rows))[0].item.sensitive

st.save_items("L3", "1", [Item(f"i{n}", b"x" * 200) for n in range(1, 21)])
folder = work / "mem" / "l3" / "1"
index = json.loads((folder / "index.airyidx").read_text(encoding="utf-8"))
assert set(index["items"]) == {f"i{n}" for n in range(1, 21)}
assert st.verify("L3", "1") == "ok"
print("format=ok index=ok")

st.remove_items("L3", "1", [f"i{n}" for n in range(1, 16)])
before = stats.level_stats(st, "L3", "1")
doc = defrag.run(st, cfg, "L3", "1", now=True)
after = doc["after"]
kept = len(st.items("L3", "1"))
assert doc["state"] == "done" and kept == 5
assert after["fragmentation_pct"] < before["fragmentation_pct"]
print(f"defrag before={before['fragmentation_pct']} "
      f"after={after['fragmentation_pct']} kept={kept}")

st.save_items("L3", "2", [Item("keep", b"a" * 8), Item("sec", b"s", True)])
st.save_items("L3", "5", [Item("old", b"o")], owner="other")
try:
    transfer.move(st, {"from": "2", "to": "3", "items": "sec"})
    raise SystemExit("sensitive 未被拒绝")
except BadState:
    print("sensitive=rejected")
transfer.clone(st, {"from": "2", "to": "3", "items": "keep"})
assert "keep" in [item.id for item in st.items("L3", "3")]
assert "keep" in [item.id for item in st.items("L3", "2")]
print("clone=ok")

st.save_items("L3", "4", [Item("e1", b"q" * 64)])
row = erase.erase(st, cfg, {"agent": "4", "items": "e1",
                            "method": "overwrite"})
assert row["passes"] == 1 and row["bytes"] >= 64
log = (work / "logs" / audit.FILE).read_text(encoding="utf-8")
assert "overwrite" in log and "gate" in log
print("erase=ok audit=ok")

try:
    erase.erase(st, cfg, {"agent": "5", "all": True, "level": "L4",
                          "method": "crypto"})
    raise SystemExit("crypto 未被拒绝")
except BadUnsupported as exc:
    assert exc.code == 3
    print("crypto=3")

small = Store(str(work / "small"), 1)
small.save_items("L3", "1", [Item(f"s{n}", b"y" * 200) for n in range(1, 9)])
print("small=ok")
PY
endgroup

# ─── 4. 命令行与失败路径 ─────────────────────────────────────────────
group "memory-ops: 命令行"
have '"items": 5' <(mem status 1 --json) "status 未报真实项数"
have 'state=done' <(mem defrag status) "defrag 状态未落盘"
rc=0; mem status 0 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 2 ] || bad "坏 Agent 号退出码 ${rc}，期望 2"
rc=0; mem status 99 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "缺记忆仓库退出码 ${rc}，期望 3"
rc=0; mem transfer --from-agent 2 --to-agent 5 --items keep \
    >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 4 ] || bad "跨属主未同意退出码 ${rc}，期望 4"
rc=0; mem erase 5 --all --level crypto >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "crypto 退出码 ${rc}，期望 3"
endgroup

# ─── 5. 配置覆盖 ─────────────────────────────────────────────────────
group "memory-ops: 配置"
printf 'airy_memory.l3_block_kb = 1\n' > "${WORK}/good.conf"
have '"block_kb": 1' <(python3 "${ENTRY}" --root "${WORK}/small" \
    --conf "${WORK}/good.conf" status 1 --json) "配置未覆盖块大小"
printf 'airy_memory.defrag_window = 99\n' > "${WORK}/bad.conf"
rc=0; python3 "${ENTRY}" --root "${WORK}/small" --conf "${WORK}/bad.conf" \
    status 1 >/dev/null 2>&1 || rc=$?
[ "${rc}" -eq 3 ] || bad "坏配置退出码 ${rc}，期望 3"
endgroup

# ─── 6. 安装接线与桩标记 ─────────────────────────────────────────────
group "memory-ops: 安装接线"
have 'bin/airy-memory' "${SYS}/CMakeLists.txt" "CMake 未安装入口"
have 'ops/memory' "${SYS}/CMakeLists.txt" "CMake 未安装运维包"
have 'Source103' "${SPEC}" "spec 未登记入口源"
have 'Source104' "${SPEC}" "spec 未登记运维包源"
have 'source-bundle ../../system/ops/memory' "${SPEC}" \
    "spec-lint 未声明运维包"
have 'airy-memory' "${SPEC}" "spec 未安装入口"
have '%\{python3_sitelib\}/memory' "${SPEC}" "spec 未打包运维模块"
have 'memory-ops\.sh' "${FLOW}" "workflow 未监听门禁脚本"
have '^  memory-verify:' "${FLOW}" "workflow 未新增 memory 作业"
for src in "${PKG}"/*.py "${ENTRY}"; do
    deny 'NotImplementedError|TODO|FIXME|breakpoint\(' "${src}" \
        "残留桩标记: ${src}"
    deny '/home/' "${src}" "残留本机绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "memory-ops 门禁失败 ${FAILS} 项"
say "memory-ops 门禁通过"
