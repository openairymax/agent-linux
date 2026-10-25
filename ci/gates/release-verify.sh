#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/release-verify.sh — 发布前对已签名物料的自我核对
#
# 用法: ci/gates/release-verify.sh [--dir DIR]
#
# 时机在 artifact 落盘之后、推 registry 与建 Release 之前。上游签名发生在
# 另一台 runner、另一份文件系统；此处重算摘要，失败即说明落盘字节已被
# artifact 打包/解包改写，继续发布等于把未经核对的东西挂上发行 tag。
#
# 校验项：
#   * 摘要清单里每一行的文件都能找到，且重算摘要与清单一致
#   * 清单内每份署名非空，且其被签文件在物料里存在
#   * release notes 恰好三个 ## 小节，正文不超过 12 行
#
# 不做 gpg --verify：发布 job 不导入私钥，keyring 里没有公钥。密码学校验
# 由取件方用发布页公钥完成（tools/airy-flash/README.md）。
#
# 退出码: 0 通过；1 物料不自证；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly NOTES_MAX=12
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

DIR="."

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)     DIR="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/release-verify.sh [--dir DIR]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -d "${DIR}" ] || { err "目录不存在: ${DIR}"; exit "${EINVAL}"; }

# .git 里不会有受签物料，剪掉它只为一件事：让 find 在 checkout 上不必
# 遍历对象库。
walk()
{
    find "${DIR}" -name .git -prune -o -type f "$@" -print
}

pick()
{
    local pattern="$1" hit
    hit="$(walk -name "${pattern}" | sort)"
    [ -n "${hit}" ] || { err "缺少${2}: ${pattern}"; return 1; }
    [ "$(printf '%s\n' "${hit}" | wc -l)" -eq 1 ] ||
        { err "${2}不唯一: ${pattern}"; return 1; }
    printf '%s\n' "${hit}"
}

sum="$(pick 'agent-linux-*.sha256sum' '摘要清单')" || exit 1
notes="$(pick 'agent-linux-*-release-notes.md' 'release notes')" || exit 1

count=0
signs=0
while read -r digest name; do
    [ -n "${name:-}" ] || continue
    hit="$(walk -name "${name}" | head -n 1)"
    [ -n "${hit}" ] || die "清单内文件缺失: ${name}"
    actual="$(sha256sum "${hit}" | cut -d' ' -f1)"
    [ "${actual}" = "${digest}" ] || die "摘要不符: ${name}"
    count=$((count + 1))
    case "${name}" in
        *.asc)
            [ -s "${hit}" ] || die "空署名: ${name}"
            [ -n "$(walk -name "${name%.asc}" | head -n 1)" ] ||
                die "署名缺少被签文件: ${name}"
            signs=$((signs + 1))
            ;;
    esac
done < "${sum}"
[ "${count}" -gt 0 ] || die "摘要清单为空: ${sum}"
[ "${signs}" -gt 0 ] || die "清单内没有任何署名"

for f in "${sum}.asc" "${notes}.asc"; do
    [ -s "${f}" ] || die "缺少署名: ${f##*/}"
done

sections="$(grep -c '^## ' "${notes}" || true)"
[ "${sections}" -eq 3 ] || die "${notes##*/} 小节数 ${sections}，应为 3"
lines="$(grep -cv '^$' "${notes}" || true)"
[ "${lines}" -le "${NOTES_MAX}" ] ||
    die "${notes##*/} 正文 ${lines} 行，上限 ${NOTES_MAX}"

say "物料自证通过: ${count} 项摘要, ${signs} 份署名, notes ${lines} 行"
