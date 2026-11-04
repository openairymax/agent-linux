#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_docs_links.sh — ci/gates/docs-links.sh 的分支回归
#
# 门禁判的是文档树里没有悬空相对链接，真实树常年为绿，判定逻辑只能靠负例
# 驱动：逐条造出跨仓链接断裂、仓内链接断裂、归档里的历史断链、代码块里的
# 假链接，断言只看退出码与稳定判据名。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh docs-links 负责那一条，本文件
# 只保证门禁没有漏判（断链被放过）与误判（把代码块、绝对 URL、页内锚点当链接）。
#
# 用法: bash ci/tests/test_docs_links.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/docs-links.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${GATE}" "${FX}/ci/gates/docs-links.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/doc_links.py" "${FX}/ci/doc_links.py"

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
    bash "${FX}/ci/gates/docs-links.sh" "$@" 2>&1
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

# 合规树：仓内链接、带锚点的仓内链接、跨仓链接（指向树外真实文件）、
# 页内锚点、绝对 URL、归档里的历史断链、代码块与行内代码里的假链接。
tree()
{
    rm -rf "${tmp}/tree" "${tmp}/code"
    mkdir -p "${tmp}/tree/sub" "${tmp}/tree/20-archive" "${tmp}/code"
    printf 'int main(void) { return 0; }\n' >"${tmp}/code/x.c"

    cat >"${tmp}/tree/a.md" <<'EOF'
# A

- 仓内：[B](b.md)
- 带锚点：[B 节](b.md#part)
- 跨仓：[源码](../code/x.c)
- 页内：[本节](#a)
- 外链：[站点](https://example.com/a.md)
- 绝对：[本机](file:///etc/hosts)
- 归档：[旧稿](20-archive/old.md)

```c
[不是链接](nope.md)
```

行内代码 `[不是链接](nope.md)` 也不算链接。
EOF

    cat >"${tmp}/tree/b.md" <<'EOF'
# B

- 下一级：[C](sub/c.md)
EOF

    printf '# C\n' >"${tmp}/tree/sub/c.md"
    printf '# 旧稿\n\n- [早年的路径](gone.md)\n' >"${tmp}/tree/20-archive/old.md"
}

tree
out="$(run --source "${tmp}/tree")"
check "合规树判通过" 0 "$?" "${out}" "悬空 0"

tree
rm -f "${tmp}/code/x.c"
out="$(run --source "${tmp}/tree")"
check "跨仓链接断裂判拒绝" 1 "$?" "${out}" "link/dangling a.md:5"

tree
printf '\n- [缺一篇](missing.md)\n' >>"${tmp}/tree/b.md"
out="$(run --source "${tmp}/tree")"
check "仓内链接断裂判拒绝" 1 "$?" "${out}" "link/dangling b.md:5"

tree
rm -f "${tmp}/tree/sub/c.md"
out="$(run --source "${tmp}/tree")"
check "子目录链接断裂判拒绝" 1 "$?" "${out}" "link/dangling b.md:3"

tree
printf '\n- [缺一篇](../outside.md)\n' >>"${tmp}/tree/sub/c.md"
out="$(run --source "${tmp}/tree")"
check "上跳越界链接断裂判拒绝" 1 "$?" "${out}" "link/dangling sub/c.md:3"

rm -rf "${tmp}/tree"
mkdir -p "${tmp}/tree/20-archive"
printf '# 现役\n' >"${tmp}/tree/a.md"
printf '# 旧稿\n\n- [早年的路径](gone.md)\n' >"${tmp}/tree/20-archive/old.md"
out="$(run --source "${tmp}/tree")"
check "归档断链不计入判拒绝" 0 "$?" "${out}" "悬空 0"

tree
printf '# 旧稿\n' >"${tmp}/tree/20-archive/old.md"
printf '\n- [缺一篇](gone.md)\n' >>"${tmp}/tree/sub/c.md"
out="$(run --source "${tmp}/tree")"
check "未排除时子目录断链判拒绝" 1 "$?" "${out}" "link/dangling sub/c.md:3"

out="$(run --source "${tmp}/tree" --exclude sub)"
check "排除项生效判拒绝" 0 "$?" "${out}" "悬空 0"

out="$(run --source "${tmp}/nope")"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run --exclude)"
check "缺 --exclude 参数退 5" 5 "$?" "${out}" "需要名字"

mv "${FX}/ci/doc_links.py" "${tmp}/doc_links.py"
out="$(run --source "${tmp}/tree")"
check "缺少检查器退 5" 5 "$?" "${out}" "缺少检查器"
mv "${tmp}/doc_links.py" "${FX}/ci/doc_links.py"

out="$(run)"
check "无文档树时跳过不假绿" 0 "$?" "${out}" "本门禁不适用"

mkdir -p "${tmp}/docs/docs-linux"
printf '# 默认树\n\n- [B](b.md)\n' >"${tmp}/docs/docs-linux/a.md"
printf '# B\n' >"${tmp}/docs/docs-linux/b.md"
out="$(run)"
check "默认源指向 docs/docs-linux" 0 "$?" "${out}" "悬空 0"

printf -- '- [缺一篇](missing.md)\n' >>"${tmp}/docs/docs-linux/a.md"
out="$(run)"
check "默认源断链判拒绝" 1 "$?" "${out}" "link/dangling a.md:4"

if [ "${fails}" -ne 0 ]; then
    printf 'test_docs_links: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_docs_links: 全部通过\n'
