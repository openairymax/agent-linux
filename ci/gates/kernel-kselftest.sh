#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-kselftest.sh — kselftest 子集构建、运行与 TAP 解析
#
# 用法:
#   ci/gates/kernel-kselftest.sh [--src DIR] [--install DIR] [--tap FILE]
#                               [--targets "seccomp pidfd membarrier rseq"]
#
# 三段逻辑此前是 ci-kernel.yml 里三个相邻的 run 块，靠 job 级 env 传
# KSFT_INSTALL。合并成一条门禁的理由：install 目录是「构建」与「运行」
# 之间的唯一契约，拆开写时任何一段单独重跑都会得到看似成功的假象
# （例如 install 目录过期，run 段照样跑旧用例并通过）。
#
# 运行在 virtme-ng 起的虚拟机里，内核用参考构建产出的 bzImage；宿主侧
# 只提供用户态。--disable-kvm 是为了在托管 runner 上稳定出结果（嵌套
# 虚拟化不可靠），本地有 KVM 时可加 --kvm 提速。
#
# 退出码: 0 通过；1 构建/运行/TAP 解析失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=kernel-kselftest
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
INSTALL="${KSFT_INSTALL:-${REPO_ROOT}/kselftest-install}"
TAP="kselftest.tap"
TARGETS="seccomp pidfd membarrier rseq"
ACCEL="--disable-kvm"

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-kselftest.sh [--src DIR] [--install DIR] [--tap FILE]
                                   [--targets "seccomp pidfd membarrier rseq"]
                                   [--kvm]

  --install  构建安装目录（缺省 $KSFT_INSTALL 或 <repo>/kselftest-install）
  --kvm      允许 virtme-ng 使用 KVM（本地有 /dev/kvm 时更快）
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --install) INSTALL="${2:-}"; shift 2 ;;
    --tap) TAP="${2:-}"; shift 2 ;;
    --targets) TARGETS="${2:-}"; shift 2 ;;
    --kvm) ACCEL=""; shift ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"
[ -n "${TARGETS}" ] || { note "--targets 不能为空"; exit "${EINVAL}"; }
[ -f "${SRC}/arch/x86/boot/bzImage" ] ||
    die "缺少 bzImage，先跑 kernel-refbuild.sh"

cd "${SRC}" || die "无法进入 ${SRC}"

group "构建并安装 kselftest 子集"
# 每次重建 install 目录，避免上一轮的残留用例被当成本轮结果。
rm -rf "${INSTALL}"
mkdir -p "${INSTALL}"
# shellcheck disable=SC2086  # TARGETS 按空格拆分是接口约定
make -C tools/testing/selftests TARGETS="${TARGETS}" \
    -j"$(nproc)" install KSFT_INSTALL_PATH="${INSTALL}" ||
    die "kselftest 构建/安装失败"
[ -f "${INSTALL}/run_kselftest.sh" ] || die "缺少 run_kselftest.sh"
[ -f "${INSTALL}/kselftest-list.txt" ] || die "缺少 kselftest-list.txt"
cat "${INSTALL}/kselftest-list.txt"
endgroup

group "virtme-ng 运行 kselftest（OS-TEST-013~022）"
set -o pipefail
(
    # run_kselftest.sh 由自身位置推导 BASE_DIR，必须从 install 目录运行。
    cd "${INSTALL}" || exit 1
    # shellcheck disable=SC2086  # ACCEL 可能为空
    vng --run "${SRC}/arch/x86/boot/bzImage" \
        --cwd "${INSTALL}" --force-9p ${ACCEL} --quiet \
        --exec "./run_kselftest.sh --summary"
) >"${TAP}" 2>&1
rc=$?
endgroup
[ "${rc}" -eq 0 ] || {
    tail -n 60 "${TAP}" >&2
    die "kselftest 运行失败（rc=${rc}）"
}

group "解析 kselftest TAP（OS-TEST-022）"
python3 "${REPO_ROOT}/tools/parse-tap.py" --input "${TAP}" --fail-on-not-ok
rc=$?
endgroup
[ "${rc}" -eq 0 ] || die "kselftest TAP 解析发现 not ok"

say "kselftest 通过（${TARGETS}）"
