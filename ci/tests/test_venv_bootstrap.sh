#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_venv_bootstrap.sh — ci/lib/pyenv.sh 与 tools/venv-bootstrap.sh
# 的分支回归
#
# 引导脚本的失效方式都很安静：指纹没算进解释器版本，系统 python 升级后沿用
# 旧环境，直到 import 报 undefined symbol；校验写成「目录存在即就绪」，于是
# 半途中断的环境永远不再补装；路径守卫漏判树内目录，构建产物悄悄进源码树。
# 这里用一棵只带引导脚本与依赖清单的假树 + 一个假 python3 覆盖这些分支，
# 不联网、不落盘到真实缓存。
#
# 用法: bash ci/tests/test_venv_bootstrap.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/lib" "${FX}/tools/demo" "${tmp}/bin"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/pyenv.sh" "${FX}/ci/lib/pyenv.sh"
cp "${ROOT}/tools/venv-bootstrap.sh" "${FX}/tools/venv-bootstrap.sh"
printf 'pytest>=8.0,<9.0\n' >"${FX}/tools/demo/requirements-dev.txt"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 假 python3：只实现引导真正调用的四个动作（-V / venv / pip / pytest），
# 把调用记进 FAKE_LOG。venv 里的解释器就是本文件的副本，因此用 $0 定位
# 自身所在的 venv 目录（dirname 两次），不依赖外部传入的变量。
cat >"${tmp}/bin/python3" <<'STUB'
#!/usr/bin/env bash
self="${BASH_SOURCE[0]}"
log="${FAKE_LOG:?}"
case "${1:-}" in
    -V) printf 'Python %s\n' "${FAKE_PY:-3.11.0}"; exit 0 ;;
    -m)
        case "${2:-}" in
            venv)
                t="${@: -1}"
                mkdir -p "${t}/bin"
                printf 'venv %s\n' "${t}" >>"${log}"
                cp "${self}" "${t}/bin/python"
                chmod +x "${t}/bin/python"
                exit 0
                ;;
            pip)
                if [ "${3:-}" = "--version" ]; then
                    printf 'pip 25.0\n'; exit 0
                fi
                [ "${3:-}" = install ] || exit 9
                printf 'install %s\n' "${@: -1}" >>"${log}"
                exit 0
                ;;
            pytest)
                [ -e "$(dirname "$(dirname "${self}")")/broken" ] && exit 1
                printf 'pytest 8.3.3\n'
                exit 0
                ;;
        esac
        ;;
esac
exit 9
STUB
chmod +x "${tmp}/bin/python3"
export FAKE_LOG="${tmp}/calls.log"
: >"${FAKE_LOG}"

# run ARGS... —— 在假树里跑引导；每条用例都从干净的环境变量出发。
run()
{
    env -u AIRYMAX_VENV -u XDG_CACHE_HOME -u GITHUB_ACTIONS \
        PATH="${tmp}/bin:${PATH}" FAKE_LOG="${FAKE_LOG}" FAKE_PY="${FAKE_PY:-3.11.0}" \
        XDG_CACHE_HOME="${tmp}/cache" \
        bash "${FX}/tools/venv-bootstrap.sh" "$@" 2>&1
}

# expect NAME WANT_RC WANT_TEXT ARGS... —— 比对退出码与输出片段。
expect()
{
    local name="$1" want_rc="$2" want_text="$3" out rc
    shift 3
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" != "${want_rc}" ]; then
        bad "${name}（rc=${rc}，期望 ${want_rc}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${want_text}" ] && ! printf '%s' "${out}" | grep -qF -- "${want_text}"; then
        bad "${name}（输出缺「${want_text}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

VENV="${tmp}/cache/airymaxos/venv"
installs() { grep -c '^install ' "${FAKE_LOG}" || true; }

expect "默认目录在仓库外" 0 "${VENV}" --print
expect "未引导时 --check 失败" 1 "未就绪" --check

expect "首次引导成功" 0 "已安装 1 份依赖清单"
[ -f "${VENV}/.airymax-env" ] && ok "写入指纹" || bad "写入指纹"
[ "$(installs)" = 1 ] && ok "装载一份清单" || bad "装载一份清单（install=$(installs)）"
expect "引导后 --check 通过" 0 "已就绪" --check

# 幂等：指纹未变时不重复安装，否则每次门禁都要联网跑一遍 pip。
before="$(installs)"
expect "二次引导幂等" 0 "已就绪"
[ "$(installs)" = "${before}" ] && ok "未重复安装" ||
    bad "未重复安装（install 从 ${before} 变成 $(installs)）"

# pytest 不可导入等同未就绪：装完依赖但半途中断的环境不能再被判为可用。
# 这一条必须排在清单与解释器变化之前——那两条会顺带触发重装，混在一起
# 就分不清是哪个判据起了作用。
touch "${VENV}/broken"
expect "pytest 不可用时重建" 0 "已安装 1 份依赖清单"
rm -f "${VENV}/broken"

# 依赖清单变化必须触发重装，否则新增的测试依赖在本地永远缺失。
printf 'pytest>=8.0,<9.0\npyyaml>=6.0,<7.0\n' >"${FX}/tools/demo/requirements-dev.txt"
expect "清单变化后重装" 0 "已安装 1 份依赖清单"

# 解释器版本变化同样要重装：旧环境里的 C 扩展与新解释器 ABI 不同。
before="$(installs)"
FAKE_PY=3.12.0 expect "解释器升级后重装" 0 "已安装 1 份依赖清单"
[ "$(installs)" -gt "${before}" ] && ok "版本变化触发重装" ||
    bad "版本变化触发重装（install 未增长）"

# 树内目录必须拒绝：虚拟环境属于构建产物，不进源码树。
out="$(run --dir "${FX}/tools/.venv")"
rc=$?
if [ "${rc}" = 1 ] && printf '%s' "${out}" | grep -qF "拒绝在源码树内"; then
    ok "拒绝树内目录"
else
    bad "拒绝树内目录（rc=${rc}）"
    printf '%s\n' "${out}" >&2
fi

# 相对路径无法判断树内树外，直接拒绝。
expect "拒绝相对路径" 1 "必须是绝对路径" --dir relative/venv

# 参数缺失按用法错误返回 5，不与校验失败混淆。
expect "缺 --dir 参数" 5 "--dir 需要一个路径" --dir
expect "未知参数" 5 "未知参数" --bogus

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
