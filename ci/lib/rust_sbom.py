#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""ci/lib/rust_sbom.py —— TUI 工作区的 CycloneDX 物料清单

由 ci/gates/rust-sbom.sh 调用，也可被回归脚本 import。

组件集合只有一个事实源：system/tui/Cargo.lock。cargo metadata 提供包名、
版本、许可证与来源，但它的包集合随解析参数（features、target）变化，而发版
要回答的是「这棵树锁住了什么」。因此两边逐条对账：数量或成员不等即失败，
而不是把某一侧的视图当成事实。AC-S3 要求的也正是这个等式。

输出必须确定：同一份锁得到逐字节相同的文件，否则摘要会在两次生成之间漂移，
「与 SBOM 同 digest」就无从判定。为此不写时间戳、序列号与任何绝对路径，
组件按 bom-ref 字节序排列，序列化参数固定。

许可证不是原样抄录：每条许可证表达式都要能在 system/tui/deny.toml 的允许
集里落地，否则清单通过而准入声明形同虚设。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

SPEC = "1.5"
WS_NAME = "airy-tui"
CRATES_IO = "https://github.com/rust-lang/crates.io-index"
USAGE = ("用法: rust_sbom.py gen TUI OUT | verify TUI | policy TUI")


def die(msg):
    """判定失败：只留一句原因，调用方据此退出 1。"""
    print("rust_sbom: " + msg, file=sys.stderr)
    raise SystemExit(1)


def read_toml(path):
    """按 TOML 读入；Cargo.lock 与清单都是 TOML，不另写解析器。"""
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def lock_rows(tui):
    """锁内每个包的四项事实；摘要与来源缺席表示工作区成员。"""
    doc = read_toml(Path(tui) / "Cargo.lock")
    return [{"name": p["name"], "ver": p["version"],
             "sum": p.get("checksum", ""), "src": p.get("source", "")}
            for p in doc.get("package", [])]


def ws_ver(tui):
    """工作区版本取自 Cargo.toml，避免在清单里再写一份。"""
    doc = read_toml(Path(tui) / "Cargo.toml")
    return doc.get("workspace", {}).get("package", {}).get("version", "")


def _json(done):
    if done.returncode:
        die("cargo metadata 失败：" + done.stderr.strip())
    return json.loads(done.stdout)


def metadata(tui):
    """离线取元数据：--locked 保证读到的就是仓库里这份锁。"""
    argv = ["cargo", "metadata", "--offline", "--locked",
            "--format-version", "1"]
    env = dict(os.environ, CARGO_TERM_COLOR="never")
    done = subprocess.run(argv, cwd=tui, env=env, capture_output=True,
                          text=True)
    return _json(done)


def purl(name, ver):
    """包 URL：registry 包的标准标识，也用作 bom-ref。"""
    return "pkg:cargo/%s@%s" % (name, ver)


def _origin(row):
    return "registry" if row["src"] else "workspace"


def _hash(row):
    return [{"alg": "SHA-256", "content": row["sum"]}] if row["sum"] else []


def _lic(pkg):
    return [{"expression": pkg["license"]}] if pkg.get("license") else []


def _opt(pkg, row):
    """可选项只在有值时出现：空数组在 CycloneDX 里与缺席不同义。"""
    pairs = (("hashes", _hash(row)), ("licenses", _lic(pkg)))
    return {k: v for k, v in pairs if v}


def component(pkg, row):
    """单个组件；摘要与许可证缺席时整项省略，而不是留空数组。"""
    ref = purl(row["name"], row["ver"])
    base = {"type": "library", "bom-ref": ref, "name": row["name"],
            "version": row["ver"], "purl": ref,
            "properties": [{"name": "airy:origin", "value": _origin(row)}]}
    return dict(base, **_opt(pkg, row))


def build(tui):
    """生成清单并当场对账：返回 (文档, 组件数)。

    数量、成员、许可证与摘要都在这里判，generate 与 verify 因此共用同一道
    门槛：产出的清单不可能是准入声明之外的实物。
    """
    rows = lock_rows(tui)
    pkgs = {(p["name"], p["version"]): p for p in metadata(tui)["packages"]}
    if len(pkgs) != len(rows):
        die("组件数不一致：metadata %d，Cargo.lock %d"
            % (len(pkgs), len(rows)))
    comps = [_comp(pkgs, row) for row in rows]
    allow = allow_list(tui)
    for comp in comps:
        _need_lic(comp, allow)
        _need_sum(comp)
    comps.sort(key=lambda c: c["bom-ref"])
    return _doc(comps, ws_ver(tui)), len(comps)


def _comp(pkgs, row):
    pkg = pkgs.get((row["name"], row["ver"]))
    if pkg is None:
        die("Cargo.lock 的 %s %s 不在 cargo metadata 中"
            % (row["name"], row["ver"]))
    return component(pkg, row)


def _doc(comps, ver):
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC,
        "version": 1,
        "metadata": {
            "tools": {"components": [{"type": "application",
                                      "vendor": "AirymaxOS",
                                      "name": "rust-sbom",
                                      "version": "1"}]},
            "component": {"type": "application", "name": WS_NAME,
                          "version": ver, "bom-ref": WS_NAME + "@" + ver},
        },
        "components": comps,
    }


def render(doc):
    """固定序列化参数：字节稳定是摘要可比的前提。"""
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()


def allow_list(tui):
    """准入声明里允许的许可证集合。"""
    doc = read_toml(Path(tui) / "deny.toml")
    return list(doc.get("licenses", {}).get("allow", []))


def _word(expr, i):
    """从 i 读一个不含空白与括号的词，返回 (词, 下一位置)。"""
    j = i
    while j < len(expr) and not expr[j].isspace() and expr[j] not in "()":
        j += 1
    return expr[i:j], j


def _skip(expr, i):
    """跳过空白，返回首个非空白位置。"""
    while i < len(expr) and expr[i].isspace():
        i += 1
    return i


def _tok(expr):
    """切成括号、运算符与标识符；WITH 例外并入前一个标识符。

    许可证表达式允许括号改变优先级，按空白切分会把「(MIT OR Apache-2.0)
    AND Unicode-3.0」拆成非法片段，从而把合法依赖判成越界。
    """
    out, i = [], 0
    while i < len(expr):
        char = expr[i]
        if char in "()":
            out.append(char)
            i += 1
        elif char.isspace():
            i += 1
        else:
            word, i = _word(expr, i)
            if word in ("AND", "OR"):
                out.append(word)
            elif word == "WITH":
                tail, i = _word(expr, _skip(expr, i))
                out[-1] = out[-1] + " WITH " + tail
            else:
                out.append(word)
    return out


def _atom(toks, pos, allow):
    """括号组或单个标识符；返回 (是否允许, 下一个位置)。"""
    if pos < len(toks) and toks[pos] == "(":
        ok, pos = _lic_or(toks, pos + 1, allow)
        if pos >= len(toks) or toks[pos] != ")":
            return False, len(toks)
        return ok, pos + 1
    return pos < len(toks) and toks[pos] in allow, pos + 1


def _lic_and(toks, pos, allow):
    """AND 组内每项都要落地。"""
    ok, pos = _atom(toks, pos, allow)
    while pos < len(toks) and toks[pos] == "AND":
        rhs, pos = _atom(toks, pos + 1, allow)
        ok = ok and rhs
    return ok, pos


def _lic_or(toks, pos, allow):
    """OR 的任一分支成立即可：取件方可以选择被允许的那一支。"""
    ok, pos = _lic_and(toks, pos, allow)
    while pos < len(toks) and toks[pos] == "OR":
        rhs, pos = _lic_and(toks, pos + 1, allow)
        ok = ok or rhs
    return ok, pos


def lic_ok(expr, allow):
    """整条表达式都要被读到：尾部残缺按越界处理。"""
    toks = _tok(expr)
    ok, pos = _lic_or(toks, 0, allow)
    return ok and pos == len(toks)


def _need_allow(doc):
    allow = doc.get("licenses", {}).get("allow", [])
    if not allow:
        die("deny.toml 未声明 [licenses].allow")
    for want in ("AGPL-3.0-or-later", "Apache-2.0"):
        if want not in allow:
            die("deny.toml 的允许集未覆盖工作区声明的 " + want)


def _need_bans(doc):
    bans = doc.get("bans", {})
    for key in ("multiple-versions", "wildcards"):
        if bans.get(key) != "deny":
            die("deny.toml 的 [bans].%s 须为 deny" % key)


def _need_src(doc):
    src = doc.get("sources", {})
    for key in ("unknown-registry", "unknown-git"):
        if src.get(key) != "deny":
            die("deny.toml 的 [sources].%s 须为 deny" % key)
    if CRATES_IO not in src.get("allow-registry", []):
        die("deny.toml 的 [sources].allow-registry 未列 crates-io")


def policy(tui):
    """准入声明自身的结构判据；不调用 cargo，可在最便宜的作业里跑。"""
    path = Path(tui) / "deny.toml"
    if not path.is_file():
        die("缺少 " + str(path))
    doc = read_toml(path)
    _need_allow(doc)
    _need_bans(doc)
    _need_src(doc)


def _need_lic(comp, allow):
    lic = comp.get("licenses", [{}])[0].get("expression", "")
    if not lic:
        die(comp["name"] + " 未声明许可证")
    if not lic_ok(lic, allow):
        die(comp["name"] + " 的许可证 " + lic + " 不在允许集内")


def _need_sum(comp):
    origin = comp["properties"][0]["value"]
    if origin == "registry" and not comp.get("hashes"):
        die(comp["name"] + " 缺少锁内摘要")


def verify(tui):
    """完整判据：准入与对账由 build 承担，这里只判确定性与路径外泄。"""
    policy(tui)
    doc, n = build(tui)
    raw = render(doc)
    if raw != render(build(tui)[0]):
        die("两次生成的清单字节不同，摘要不可比")
    if str(Path(tui).resolve()).encode() in raw:
        die("清单含本地绝对路径")
    print("components=%d bytes=%d" % (n, len(raw)))


def main(argv):
    """三个动作：生成、判定、准入声明自检。"""
    if len(argv) < 3:
        print(USAGE, file=sys.stderr)
        return 2
    if argv[1] == "gen" and len(argv) == 4:
        Path(argv[3]).write_bytes(render(build(Path(argv[2]))[0]))
        return 0
    acts = {"verify": verify, "policy": policy}
    if argv[1] in acts and len(argv) == 3:
        acts[argv[1]](Path(argv[2]))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
