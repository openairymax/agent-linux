#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/tool-pack.sh — 发行物料打包：SDK 与烧录工具
#
# 用法: ci/gates/tool-pack.sh --out DIR --version V
#
# 产物两份，取件方下载即用：
#   agent-linux-sdk-<ver>.tar.gz   语言绑定（sdk/）
#   airy-flash-<ver>.pyz           烧录工具单文件（tools/airy-flash/）
#
# 确定性：同一份源码两次打包必须字节相同，否则摘要清单（release-verify.sh
# 逐项重算）在重发时永远对不上。tar 按名排序、mtime 取 SOURCE_DATE_EPOCH、
# 属主归零；gzip 用 -n 不写时间与文件名。pyz 是「shebang + zip」：条目按名
# 排序、时间戳固定 1980-01-01、权限 0644/0755、ZIP_STORED 不压缩。
# 打包两遍互相比对，把「确定性」从注释里的承诺变成门禁里的断言。
#
# 打包后执行一次 pyz 并用 --version 对账：发布页上挂一份「下载即崩」的烧录
# 工具比不发布更糟——它会先把人的 U 盘写坏才暴露。
#
# 退出码: 0 通过；1 打包、自检或确定性比对失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly SRC="tools/airy-flash"
readonly MODS=(airy_flash.py flash_types.py)
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
OUT=""
VER=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)     OUT="${2:-}"; shift 2 ;;
        --version) VER="${2:-}"; shift 2 ;;
        -h|--help) sed -n '/^# 用法:/s/^# //p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${OUT}" ] || { err "缺少 --out"; exit "${EINVAL}"; }
[ -n "${VER}" ] || { err "缺少 --version"; exit "${EINVAL}"; }
need_cmd python3 tar gzip

mkdir -p "${OUT}" || die "无法创建输出目录: ${OUT}"
cd "${ROOT}" || die "无法进入仓库根: ${ROOT}"

[ -d sdk ] || die "缺少 sdk/（语言绑定目录）"
for m in "${MODS[@]}"; do
    [ -f "${SRC}/${m}" ] || die "缺少源文件: ${SRC}/${m}"
done

epoch="${SOURCE_DATE_EPOCH:-0}"
case "${epoch}" in
    ''|*[!0-9]*) die "SOURCE_DATE_EPOCH 须为非负整数: ${epoch}" ;;
esac

SDK="agent-linux-sdk-${VER}.tar.gz"
PYZ="airy-flash-${VER}.pyz"

# pyz = shebang + 全 STORED 的 zip。zipfile 记录的是文件内绝对偏移，前置于
# 归档的 shebang 不影响 zipimport 定位，这一点由 build 之后的实跑自检证明。
pack_pyz()
{
    python3 - "${SRC}" "$1" "${MODS[@]}" <<'PY' || return 1
import stat
import sys
import zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
entry = b"from airy_flash import main\nimport sys\n\nsys.exit(main())\n"
members = [("__main__.py", entry, 0o755)]
members += [(m, (src / m).read_bytes(), 0o644) for m in sys.argv[3:]]
members.sort(key=lambda item: item[0])

stamp = (1980, 1, 1, 0, 0, 0)
with out.open("wb") as fh:
    fh.write(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(fh, "w", zipfile.ZIP_STORED) as zf:
        for name, data, mode in members:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, data)
PY
}

build()
{
    local dir="$1"

    tar --format=gnu --sort=name --mtime="@${epoch}" \
        --owner=0 --group=0 --numeric-owner \
        -cf - sdk | gzip -n -9 >"${dir}/${SDK}" || return 1
    pack_pyz "${dir}/${PYZ}" || return 1
    chmod 0755 "${dir}/${PYZ}"
}

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

group "Pack SDK + airy-flash"
build "${OUT}" || die "打包失败: ${OUT}"
build "${work}" || die "复算打包失败: ${work}"
for f in "${SDK}" "${PYZ}"; do
    cmp -s "${OUT}/${f}" "${work}/${f}" || die "${f} 两次打包不一致（确定性被破坏）"
    say "${f} $(wc -c <"${OUT}/${f}") 字节 $(sha256sum "${OUT}/${f}" | cut -d' ' -f1)"
done
endgroup

group "Self-check airy-flash"
got="$(python3 "${OUT}/${PYZ}" --version 2>&1)" || die "${PYZ} 无法执行: ${got}"
[ "${got}" = "airy-flash ${VER}" ] ||
    die "${PYZ} 自报「${got}」，应为「airy-flash ${VER}」（TOOL_VERSION 未随发行版号更新？）"
say "${PYZ} 可执行，自报 ${got}"
endgroup
