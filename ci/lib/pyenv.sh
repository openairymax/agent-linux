#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/pyenv.sh — 仓库外 Python 虚拟环境的唯一实现
#
# 为什么在仓库外：本仓不在源码树内产生任何构建产物，而虚拟环境里的
# site-packages、字节码头与 pip 缓存都算产物；放在树内既污染 git status，
# 也会被 os-chk 的文件扫描当成源码。默认落在
# ${XDG_CACHE_HOME:-$HOME/.cache}/airymaxos/venv，可用 AIRYMAX_VENV 覆盖。
#
# 为什么需要它：发行版把系统 python3 标成 externally-managed（PEP 668），
# `pip install` 直接拒绝；ensurepip 还可能被拆成单独的包。0.0.3 因此只有
# CI 装了 pytest，tools/ 下的用例在开发机上跑不起来（DEV-01）。引导逻辑
# 收在这里，门禁 ci/ci.sh python-verify 与 tools/venv-bootstrap.sh 共用，
# 「本地装的依赖」与「门禁用的依赖」不允许分叉成两份。
#
# 依赖：调用方须先 source ci/lib/log.sh（say/die/group/endgroup）。
# ===========================================================================

# pyenv_home —— 默认虚拟环境目录（仓库外）
pyenv_home()
{
    printf '%s\n' "${AIRYMAX_VENV:-${XDG_CACHE_HOME:-${HOME}/.cache}/airymaxos/venv}"
}

# pyenv_guard DIR ROOT —— 拒绝对源码树内或危险位置建环境
#
# 先校验绝对路径再补父目录：相对路径的父目录会落在调用者的当前目录里，
# 先建目录就会在源码树里留下一个待拒绝的残骸。
pyenv_guard()
{
    local dir="$1" root="$2" real

    case "${dir}" in
        /*) ;;
        *) die "虚拟环境路径必须是绝对路径: ${dir}" ;;
    esac
    mkdir -p "$(dirname "${dir}")" || die "无法创建 ${dir} 的父目录"
    # 按已存在的父目录解析真实路径再拼回末段，符号链接绕路也逃不过树内判定。
    real="$(cd "$(dirname "${dir}")" && pwd -P)/$(basename "${dir}")"
    case "${real}" in
        "${root}"/*)
            die "拒绝在源码树内创建虚拟环境: ${real}（本仓不保存构建产物）" ;;
        /|"${HOME}")
            die "拒绝把虚拟环境直接建在 ${real}" ;;
    esac
}

# pyenv_fp ROOT —— 依赖指纹：清单内容与解释器版本一起构成
#
# 解释器版本必须进指纹：C 扩展按 ABI 编到 venv 的 site-packages 里，
# 系统 python 从小版本升级后沿用旧目录，症状是 import 时报
# undefined symbol，而不是提示重装依赖。
pyenv_fp()
{
    local root="$1" f

    {
        python3 -V
        for f in "${root}"/tools/*/requirements-dev.txt; do
            [ -f "${f}" ] || continue
            printf '%s ' "${f#"${root}/"}"
            sha256sum "${f}"
        done
    } | sha256sum | cut -c1-32
}

# pyenv_has_pip DIR —— 环境内是否有可用的 pip
pyenv_has_pip()
{
    "$1/bin/python" -m pip --version >/dev/null 2>&1
}

# pyenv_ok DIR ROOT —— 已就绪：指纹相符且 pytest 可导入
pyenv_ok()
{
    local dir="$1" root="$2" want

    [ -x "${dir}/bin/python" ] || return 1
    want="$(sed -n 's/^fingerprint=//p' "${dir}/.airymax-env" 2>/dev/null)"
    [ "${want}" = "$(pyenv_fp "${root}")" ] || return 1
    "${dir}/bin/python" -m pytest --version >/dev/null 2>&1
}

# pyenv_make DIR —— 建虚拟环境，两条路径都试
pyenv_make()
{
    local dir="$1"

    rm -rf "${dir}"
    # 首选标准 venv：CI 与多数发行版由 ensurepip 装好 pip。
    python3 -m venv "${dir}" >/dev/null 2>&1 || true
    if ! pyenv_has_pip "${dir}"; then
        # ensurepip 被拆包（Debian 的 python3-venv）时，venv 里没有 pip。
        # 退到 --without-pip 并继承系统 site-packages：`python -m pip`
        # 解析到系统的 pip，而 pip 的安装目标仍是本 venv 的 sys.prefix，
        # 依赖落在仓库外的环境里，不碰系统。
        rm -rf "${dir}"
        python3 -m venv --without-pip --system-site-packages "${dir}" \
            >/dev/null 2>&1 || true
    fi
    pyenv_has_pip "${dir}" ||
        die "无法准备 pip：请安装 python3-venv（Debian/Ubuntu）或 python3-pip（Fedora）后重试"
}

# pyenv_fill DIR ROOT —— 装齐 tools/*/requirements-dev.txt 并写指纹
pyenv_fill()
{
    local dir="$1" root="$2" f n=0

    for f in "${root}"/tools/*/requirements-dev.txt; do
        [ -f "${f}" ] || continue
        "${dir}/bin/python" -m pip install -q --disable-pip-version-check \
            -r "${f}" || die "依赖安装失败: ${f#"${root}/"}"
        n=$(( n + 1 ))
    done
    [ "${n}" -gt 0 ] || die "没有找到任何 tools/*/requirements-dev.txt"
    "${dir}/bin/python" -m pytest --version >/dev/null 2>&1 ||
        die "虚拟环境里没有 pytest：依赖清单未覆盖测试框架"
    printf 'fingerprint=%s\n' "$(pyenv_fp "${root}")" >"${dir}/.airymax-env"
    say "已安装 ${n} 份依赖清单 → ${dir}"
}

# pyenv_ensure DIR ROOT —— 幂等入口：已就绪直接返回，否则建好装齐
pyenv_ensure()
{
    local dir="$1" root="$2"

    pyenv_guard "${dir}" "${root}"
    if pyenv_ok "${dir}" "${root}"; then
        say "Python 环境已就绪: ${dir}"
        return 0
    fi
    group "Python 环境引导"
    pyenv_make "${dir}"
    pyenv_fill "${dir}" "${root}"
    endgroup
}
