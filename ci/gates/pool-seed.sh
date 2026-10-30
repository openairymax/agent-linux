#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/pool-seed.sh — CI 侧注入跨机收割的安装闭包池
#
# 用法: ci/gates/pool-seed.sh --dir DIR [--ref REF] [--token TOKEN]
#
# 为什么跨机播种：安装闭包约 2.5 GB，GitHub 托管的 runner（Azure 出口）
# 到国内镜像实测只有 16–22 KiB/s，180 分钟的组装预算下不完，image-build
# 因此连续超时；同一份闭包在国内验证机上到清华/华为镜像实测 6–7 MB/s，
# 几分钟就收完（收割入口 ci/gates/closure-pool.sh）。收割产物推到
# ghcr.io，CI 在缓存池为空时把它取回来，容器内的 closure.sh 便能以
# cost=1 的本地仓库命中，不再跨境拉包。
#
# 为什么必须校验：池里的 RPM 会直接进安装载荷，装到用户机器上。传输
# 出错、制品被覆盖或解包截断都会产出「装得上、运行期才炸」的镜像，而
# 装机前没有任何一步会再检查。因此制品随件一份 sha256 清单，逐项
# sha256sum -c 通过之前一个 RPM 都不许进 DIR：先在临时目录校验，再整体
# 移入，失败时 DIR 保持原样。
#
# 播种端 runbook（一台有 dnf、createrepo_c、oras 的快机器）：
#   1) ci/ci.sh closure-pool --out /var/tmp/closure-pool
#   2) 打平铺 tar 并生成清单，然后推送（oras 只收相对路径，绝对路径会
#      被拒绝且报错与传输无关，故先 cd 进目录）：
#        cd /var/tmp/closure-pool/payload/airymax/Packages
#        tar -cf /var/tmp/pool-24.03.tar .
#        sha256sum *.rpm > /var/tmp/pool-manifest.txt
#        cd /var/tmp
#        oras push --artifact-type application/vnd.airymaxos.closure.v1 \
#            ghcr.io/openairymax/airymaxos-closure:24.03-x86_64 \
#            pool-24.03.tar pool-manifest.txt
#      清单每行「<sha256>  <文件名>」，与 sha256sum -c 兼容；tar 内 RPM
#      必须平铺在顶层（上面的 -C 隐式成立），路径与清单文件名一致。
#   3) 播种端不得写闭包清单：CLOSURE_LOCK_WRITE 保持 0（closure-pool 已
#      强制），清单仍只在 CI 或显式要求时由一次完整事务写出。
#
# 制品尚未发布不是错误：照常回落在 CI 内在线求解，等制品推送后下一轮
# 自然命中。其它失败（鉴权、网络、清单不符）必须让 job 失败，避免把
# 「没播种」伪装成「播种成功」而拖到组装段超时。
#
# 退出码: 0 注入成功/池非空/制品未发布；1 拉取或校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly REF_DEFAULT="ghcr.io/openairymax/airymaxos-closure:24.03-x86_64"
readonly MANIFEST="pool-manifest.txt"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

DIR=""
REF="${REF_DEFAULT}"
TOKEN=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)   DIR="${2:-}"; shift 2 ;;
        --ref)   REF="${2:-}"; shift 2 ;;
        --token) TOKEN="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/pool-seed.sh --dir DIR [--ref REF] [--token TOKEN]"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }
# 缓存未命中时 restore 步不会建目录；播种自己建，调用方就不必再写一行
# shell（workflows 只允许单行 run）。
mkdir -p "${DIR}" || die "无法创建池目录: ${DIR}"

# 非空判据与 ci/gates/pool-state.sh 同源：池里只要有一个 *.rpm 就说明
# 上一轮已经注入过，本轮不再碰网络。
n="$(find "${DIR}" -type f -name '*.rpm' | wc -l | tr -d ' ')"
if [ "${n}" -gt 0 ]; then
    say "池非空（${n} 个 RPM），跳过播种"
    exit 0
fi

need_cmd oras sha256sum tar find

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
host="${REF%%/*}"

if [ -n "${TOKEN}" ]; then
    if ! printf '%s\n' "${TOKEN}" |
        oras login "${host}" --username airymax --password-stdin \
            >"${tmp}/login.log" 2>&1; then
        cat "${tmp}/login.log" >&2
        die "oras 登录失败: ${host}"
    fi
fi

if ! oras pull "${REF}" -o "${tmp}/pull" >"${tmp}/pull.log" 2>&1; then
    # 「还没推送」是正常状态：在线求解仍能把镜像做出来，只是慢。
    if grep -qiE 'not found|404|manifest unknown' "${tmp}/pull.log"; then
        say "种子制品尚未发布（${REF}），继续在线求解"
        exit 0
    fi
    cat "${tmp}/pull.log" >&2
    die "oras 拉取失败: ${REF}"
fi

[ -f "${tmp}/pull/${MANIFEST}" ] || die "制品缺少校准清单: ${MANIFEST}"

stage="${tmp}/stage"
mkdir -p "${stage}"
for tar in "${tmp}/pull"/*.tar; do
    [ -f "${tar}" ] || continue
    tar -xf "${tar}" -C "${stage}" || die "解包失败: ${tar}"
done
# 制品也可能直接推 RPM 而不打 tar，两种形态都要能播种。
find "${tmp}/pull" -maxdepth 1 -type f -name '*.rpm' \
    -exec cp -p {} "${stage}/" \;
[ -n "$(find "${stage}" -type f -name '*.rpm' | head -n 1)" ] ||
    die "制品内没有 RPM: ${REF}"

if ! ( cd "${stage}" &&
    sha256sum --quiet -c "${tmp}/pull/${MANIFEST}" ) \
    >"${tmp}/check.log" 2>&1; then
    cat "${tmp}/check.log" >&2
    die "sha256 校验失败，不注入（${REF}）"
fi

moved=0
while IFS= read -r rpm; do
    mv "${rpm}" "${DIR}/" || die "注入失败: ${rpm}"
    moved=$((moved + 1))
done < <(find "${stage}" -maxdepth 1 -type f -name '*.rpm' | sort)
say "注入 ${moved} 个 RPM（${REF}）"
say "sha256 全部一致"
