#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/build-provenance.sh — 构建来源指纹接线门禁（REL-03）
#
# 用法:
#   ci/gates/build-provenance.sh --source DIR
#
# 背景：同一版本号会随每次提交产出不同镜像，而镜像文件名经下载、改名、过
# U 盘之后不再可追溯。0.0.4 用一条指纹把七处接线串起来：配方模板留空 →
# 驱动解析并注入宏 → 配方写入 os-release 与 /etc/airymaxos-build → 镜像
# 清单搬运 → image-verify 两侧比对 → 运行期探针 T18 回读。
#
# 任何一处被「顺手简化」掉，构建照样成功，只有装机现场才会发现这台机器
# 说不出来自哪一轮。故本门禁只做静态接线断言：不判定指纹取值（那是构建期
# 与 %check 的事），只判定这条链没有断口——宏仍可注入、清单仍写出、探针
# 仍登记、比对仍存在。
#
# 退出码: 0 通过；1 接线断裂；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

SOURCE=""
FAILS=0

usage()
{
    cat <<'EOF'
用法: ci/gates/build-provenance.sh --source DIR
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            [ -n "${2:-}" ] || { err "--source 需要目录路径"; exit "${EINVAL}"; }
            SOURCE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${SOURCE}" ] || { err "--source 必填"; usage >&2; exit "${EINVAL}"; }
[ -d "${SOURCE}" ] || { err "源目录不存在: ${SOURCE}"; exit "${EINVAL}"; }

bad() { err "$*"; FAILS=$((FAILS + 1)); }

# need_file FILE —— 缺文件只报一次，后续判据跳过，避免同一缺失刷屏。
need_file()
{
    [ -f "$1" ] && return 0
    bad "缺文件: ${1#"${SOURCE}"/}"
    return 1
}

# has FILE TEXT LABEL —— 逐字出现即通过（含子串，够用且不引入正则歧义）。
has()
{
    grep -qF -- "$2" "$1" || bad "$3"
}

# rex FILE REGEX LABEL —— 整行匹配，用于函数定义这类「只认一行」的接线。
rex()
{
    grep -qxE -- "$2" "$1" || bad "$3"
}

# 仓库模板必须留空：写死取值会让每次构建都报告同一个来源，比没有更难查。
chk_rel()
{
    local f="${SOURCE}/distro/release/os-release"
    need_file "${f}" || return 0
    rex "${f}" 'BUILD_ID=""' "os-release: BUILD_ID 模板应为空"
    [ "$(grep -c '^BUILD_ID=' "${f}")" -eq 1 ] ||
        bad "os-release: BUILD_ID 应恰出现一次"
}

# 驱动侧：四级回退与合法性判定是同一处实现，清单与宏都从它取值。
chk_lib()
{
    local f="${SOURCE}/distro/scripts/lib/buildid.sh"
    need_file "${f}" || return 0
    rex "${f}" 'airy_build_id\(\)$' "buildid: 缺少 airy_build_id"
    rex "${f}" 'airy_bid_ok\(\)$' "buildid: 缺少 airy_bid_ok"
    has "${f}" 'A-Za-z0-9._+-' "buildid: 未约束指纹字符集"
}

# 清单里的 build_id 是发布侧唯一的取值来源，宏注入必须与它同源同次。
chk_rpms()
{
    local f="${SOURCE}/distro/scripts/build-rpms.sh"
    need_file "${f}" || return 0
    has "${f}" 'scripts/lib/buildid.sh' "build-rpms: 未加载来源指纹库"
    has "${f}" 'BUILD_ID="$(airy_build_id' "build-rpms: 未解析指纹"
    has "${f}" 'DEFS+=("airy_build_id ${BUILD_ID}")' "build-rpms: 未注入宏"
    has "${f}" "printf 'build_id=%s\n' \"\$BUILD_ID\"" \
        "build-rpms: 清单未写出指纹"
    rex "${f}" '^[[:space:]]*set_build_id$' "build-rpms: 未在装配前解析指纹"
}

# 配方侧三处：宏缺省不可静默通过、两处写入、%files 落点。
chk_spec()
{
    local f="${SOURCE}/distro/specs/airymaxos-release.spec"
    need_file "${f}" || return 0
    has "${f}" '%{!?airy_build_id:%global airy_build_id unset}' \
        "spec: 宏缺省值未设为 unset"
    has "${f}" 'BUILD_ID="%{airy_build_id}"' "spec: 未改写 os-release"
    has "${f}" '%{_sysconfdir}/airymaxos-build' "spec: 未写出指纹文件"
    has "${f}" "test '%{airy_build_id}' != unset" "spec: 配方未断言宏注入"
}

# 镜像清单只搬运、不重新推导：两处各自推导在跳过 rpm 段复跑时会分叉。
chk_img()
{
    local f="${SOURCE}/distro/ci/run-image-ci.sh"
    need_file "${f}" || return 0
    rex "${f}" 'bid_get\(\)$' "image-ci: 缺少 bid_get"
    has "${f}" 'bid:-unset' "image-ci: 清单未搬运指纹"
}

# 组装产物门禁：空值与两侧不一致都必须判失败，否则清单与镜像可各说各话。
chk_verify()
{
    local f="${SOURCE}/ci/gates/image-verify.sh"
    need_file "${f}" || return 0
    has "${f}" '缺少构建来源指纹' "image-verify: 未拒绝空指纹"
    has "${f}" '来源指纹不一致' "image-verify: 未比对两侧指纹"
}

# 容器边界：不把这组变量送进容器，驱动只能回落到 git 或配方摘要。
chk_asm()
{
    local f="${SOURCE}/ci/gates/assemble.sh"
    need_file "${f}" || return 0
    has "${f}" 'for v in AIRYMAX_BUILD_ID AIRYMAX_RUN_ID AIRYMAX_COMMIT' \
        "assemble: 未转发指纹变量"
    has "${f}" 'envs+=(-e "${v}=${!v}")' "assemble: 未注入容器环境"
}

# 云端取值：流水线是 run id + 提交 sha 的唯一提供者。
chk_wf()
{
    local f="${SOURCE}/.github/workflows/image-build.yml"
    need_file "${f}" || return 0
    has "${f}" 'AIRYMAX_RUN_ID: ${{ github.run_id }}' "workflow: 未传 run id"
    has "${f}" 'AIRYMAX_COMMIT: ${{ github.sha }}' "workflow: 未传提交号"
}

# 运行期回读：探针登记与计划数必须同时改，少一处 TAP 就会被判为不完整。
chk_smoke()
{
    local f="${SOURCE}/distro/ci/run-smoke.sh"
    need_file "${f}" || return 0
    rex "${f}" '^t18_build_id\(\) \{' "smoke: 缺少 t18_build_id"
    has "${f}" 'probe 18 "T18 build provenance" t18_build_id' \
        "smoke: T18 未登记"
    has "${f}" '1..18' "smoke: 计划数未更新"
}

say "构建来源接线 ${SOURCE}"
chk_rel
chk_lib
chk_rpms
chk_spec
chk_img
chk_verify
chk_asm
chk_wf
chk_smoke

if [ "${FAILS}" -ne 0 ]; then
    err "构建来源接线断裂 ${FAILS} 项"
    exit 1
fi

say "构建来源接线通过"
exit 0
