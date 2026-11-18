#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_rust_sbom.sh — 物料清单判据的回归
#
# 夹具里放一棵两包的工作区，并把 cargo 换成一个只吐固定元数据的脚本：
# 判据关心的是「锁与元数据对账、许可证落地、产物无本地路径」，这些都能
# 在无 Rust 工具链的机器上判，不该因为 CI 装了 rustup 才能测。
#
# 五条负例各自对应一类真实故障：组件数漂移、许可证越界、本地路径外泄、
# 准入声明缺段、参数不满足。少一条，对应的判据就可以被悄悄删掉。
#
# 用法: bash ci/tests/test_rust_sbom.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

unset LD_LIBRARY_PATH

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
TUI="${FX}/system/tui"
BIN="${tmp}/bin"
SUM="0000000000000000000000000000000000000000000000000000000000000000"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${TUI}" "${BIN}"
cp "${ROOT}/ci/gates/rust-sbom.sh" "${FX}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
cp "${ROOT}/ci/lib/rust_sbom.py" "${FX}/ci/lib/"

cat >"${BIN}/cargo" <<'SH'
#!/usr/bin/env bash
cat "${FAKE_META}"
SH
chmod +x "${BIN}/cargo"

cat >"${TUI}/Cargo.toml" <<'TOML'
[workspace]
members = ["crates/*"]

[workspace.package]
version = "0.1.0"
TOML

cat >"${TUI}/Cargo.lock" <<TOML
version = 4

[[package]]
name = "airy-tui-exec"
version = "0.1.0"

[[package]]
name = "bitflags"
version = "2.6.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "${SUM}"
TOML

cat >"${TUI}/deny.toml" <<'TOML'
[advisories]
version = 2
yanked = "deny"

[licenses]
version = 2
allow = ["MIT", "Apache-2.0", "AGPL-3.0-or-later"]

[bans]
multiple-versions = "deny"
wildcards = "deny"

[sources]
unknown-registry = "deny"
unknown-git = "deny"
allow-registry = ["https://github.com/rust-lang/crates.io-index"]
TOML

meta()
{
    cat >"${tmp}/meta.json" <<JSON
{"packages": [{"name": "airy-tui-exec", "version": "0.1.0",
               "license": "$1"},
              {"name": "bitflags", "version": "2.6.0",
               "license": "$2"}]}
JSON
}

# 正常值：工作区双许可，依赖 MIT OR Apache-2.0。
meta "AGPL-3.0-or-later OR Apache-2.0" "MIT OR Apache-2.0"

run()
{
    PATH="${BIN}:${PATH}" FAKE_META="${tmp}/meta.json" \
        bash "${FX}/ci/gates/rust-sbom.sh" "$@" 2>&1
}

fail()
{
    printf 'FAIL %s\n%s\n' "$1" "$2"
    fails=$((fails + 1))
}

# check NAME WANT_RC WANT_TEXT -- ARGS...
check()
{
    local name="$1" want_rc="$2" want_txt="$3" out rc
    shift 4
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" -ne "${want_rc}" ]; then
        fail "${name}: rc=${rc} want ${want_rc}" "${out}"
        return
    fi
    case "${out}" in
        *"${want_txt}"*) ;;
        *) fail "${name}: 输出缺「${want_txt}」" "${out}" ;;
    esac
}

check "两包工作区" 0 "components=2" -- verify
check "准入声明自检" 0 "通过" -- policy

meta "GPL-3.0-only" "MIT OR Apache-2.0"
check "许可证越界" 1 "不在允许集内" -- verify

# SPDX 表达式允许括号改变优先级；把「(MIT OR Apache-2.0) AND Unicode-3.0」
# 按空白切分会拆成非法片段，合法依赖因此被误判。两侧各取一例钉住语义：
# AND 组内每项都要落地，OR 组内任一支落地即可。
cp "${TUI}/deny.toml" "${tmp}/deny.bak"
sed -i 's/"Apache-2.0",/"Apache-2.0", "Unicode-3.0",/' "${TUI}/deny.toml"
meta "AGPL-3.0-or-later OR Apache-2.0" "(MIT OR Apache-2.0) AND Unicode-3.0"
check "括号 AND 落地" 0 "components=2" -- verify
sed -i 's/ "Unicode-3.0",//' "${TUI}/deny.toml"
check "括号 AND 越界" 1 "不在允许集内" -- verify
cp "${tmp}/deny.bak" "${TUI}/deny.toml"

# 旧式斜杠写法在 SPDX 2.x 已废弃，语义为 OR；早期 crate 仍在使用。不认它
# 会把 MIT/Apache-2.0 的依赖判成越界，认错方向则会把 GPL 也放进来。
meta "AGPL-3.0-or-later OR Apache-2.0" "Apache-2.0/MIT"
check "斜杠旧式或" 0 "components=2" -- verify
meta "AGPL-3.0-or-later OR Apache-2.0" "GPL-3.0-only/Unicode-3.0"
check "斜杠各支皆越界" 1 "不在允许集内" -- verify
meta "AGPL-3.0-or-later OR Apache-2.0" "MIT OR Apache-2.0"

# 锁与元数据同时被写成带本机路径的包名：许可证仍合法、数量仍相等，判据
# 只能靠「产物无绝对路径」拦住。这类外泄在评审里看不出来，只有断言能拦。
meta "AGPL-3.0-or-later OR Apache-2.0" "MIT OR Apache-2.0"
cp "${TUI}/Cargo.lock" "${tmp}/lock.bak"
python3 - "${tmp}/meta.json" "${TUI}/Cargo.lock" "${TUI}" <<'PY'
import json
import sys
meta_path, lock_path, leak = sys.argv[1:]
doc = json.load(open(meta_path, encoding="utf-8"))
doc["packages"][0]["name"] = leak
doc["packages"][0]["license"] = "MIT"
json.dump(doc, open(meta_path, "w", encoding="utf-8"))
text = open(lock_path, encoding="utf-8").read()
open(lock_path, "w", encoding="utf-8").write(
    text.replace('name = "airy-tui-exec"', 'name = "%s"' % leak))
PY
check "本地路径外泄" 1 "绝对路径" -- verify
cp "${tmp}/lock.bak" "${TUI}/Cargo.lock"

meta "AGPL-3.0-or-later OR Apache-2.0" "MIT"
python3 - "${tmp}/meta.json" <<'PY'
import json
import sys
path = sys.argv[1]
doc = json.load(open(path, encoding="utf-8"))
pkg = dict(doc["packages"][1], name="serde", version="1.0.0")
doc["packages"].append(pkg)
json.dump(doc, open(path, "w", encoding="utf-8"))
PY
check "组件数 drift" 1 "组件数不一致" -- verify

meta "AGPL-3.0-or-later OR Apache-2.0" "MIT OR Apache-2.0"
sed -i 's/, "AGPL-3.0-or-later"//' "${TUI}/deny.toml"
check "允许集缺口" 1 "未覆盖" -- policy

check "参数不满足" 5 "需要 --ver" -- generate
check "未知子命令" 5 "未知参数" -- frobnicate
check "帮助" 0 "用法:" -- --help

mkdir -p "${tmp}/out"
check "生成" 0 "rust.cdx.json" -- generate --ver 9.9.9 --out "${tmp}/out"
[ -f "${tmp}/out/sbom-agent-linux-9.9.9-rust.cdx.json" ] ||
    fail "生成: 文件名不符合发版约定" "$(ls "${tmp}/out")"

if [ "${fails}" -eq 0 ]; then
    echo "test_rust_sbom: 14 项通过"
    exit 0
fi
exit 1
