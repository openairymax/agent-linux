#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-kselftest.sh — kselftest 子集构建、运行与 TAP 解析
#
# 用法:
#   ci/gates/kernel-kselftest.sh [--src DIR] [--install DIR] [--tap FILE]
#                               [--targets "seccomp pidfd membarrier rseq"]
#                               [--timeout SEC] [--wall SEC] [--kvm|--no-kvm]
#
# 三段逻辑此前是 kernel-verify.yml 里三个相邻的 run 块，靠 job 级 env 传
# KSFT_INSTALL。合并成一条门禁的理由：install 目录是「构建」与「运行」
# 之间的唯一契约，拆开写时任何一段单独重跑都会得到看似成功的假象
# （例如 install 目录过期，run 段照样跑旧用例并通过）。
#
# 运行在 virtme-ng 起的虚拟机里，内核用参考构建产出的 bzImage；宿主侧
# 只提供用户态。加速方式自适应：/dev/kvm 可用则交给 vng 自行启用，
# 否则退回纯 TCG（托管 runner 上嵌套虚拟化不可靠）。
#
# 超时预算必须按「模拟环境」而非「裸机」设定。上游 runner.sh 的
# kselftest_default_timeout=45 是裸机口径；pidfd_test 自身含多段
# sleep(3) 与自旋等待，在 TCG 下累计耗时必然击穿 45s，表现为
#   not ok 1 - selftests: pidfd: pidfd_test # TIMEOUT 45 seconds
# 且因 stdio 全缓冲被 SIGKILL 截断，TAP 里零用例输出（无法据此区分
# 死锁与慢）。故门禁显式透传 -o/--override-timeout，并给整段运行加
# 墙钟上界：真死锁仍在有界时间内失败，不会拖到 job 级超时。
#
# 退出码: 0 通过；1 构建/运行/TAP 解析失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${REPO_ROOT}/kernel"
INSTALL="${KSFT_INSTALL:-${REPO_ROOT}/kselftest-install}"
TAP="kselftest.tap"
TARGETS="seccomp pidfd membarrier rseq"
# 单用例预算（秒）。上游裸机缺省 45，模拟环境按 240 取整留出充裕余量。
TIMEOUT="${KSFT_TIMEOUT:-240}"
# 整批墙钟上界（秒），防止死锁把 job 拖到 60min 超时。
WALL="${KSFT_WALL:-1200}"
ACCEL=""
ACCEL_SET=""

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-kselftest.sh [--src DIR] [--install DIR] [--tap FILE]
                                   [--targets "seccomp pidfd membarrier rseq"]
                                   [--timeout SEC] [--wall SEC]
                                   [--kvm|--no-kvm]

  --install  构建安装目录（缺省 $KSFT_INSTALL 或 <repo>/kselftest-install）
  --timeout  单用例超时（缺省 $KSFT_TIMEOUT 或 240 秒）
  --wall     整批墙钟上界（缺省 $KSFT_WALL 或 1200 秒）
  --kvm      强制使用 KVM，无 /dev/kvm 时报错
  --no-kvm   强制纯 TCG 模拟
EOF
}

is_uint()
{
    case "$1" in
    '' | *[!0-9]*) return 1 ;;
    esac
    [ "$1" -gt 0 ]
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --install) INSTALL="${2:-}"; shift 2 ;;
    --tap) TAP="${2:-}"; shift 2 ;;
    --targets) TARGETS="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    --wall) WALL="${2:-}"; shift 2 ;;
    --kvm) ACCEL=""; ACCEL_SET=1; shift ;;
    --no-kvm) ACCEL="--disable-kvm"; ACCEL_SET=1; shift ;;
    *)
        note "未知参数: $1"
        usage >&2
        exit "${EINVAL}"
        ;;
    esac
done

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"
[ -n "${TARGETS}" ] || { note "--targets 不能为空"; exit "${EINVAL}"; }
is_uint "${TIMEOUT}" || { note "--timeout 需为正整数: ${TIMEOUT}"; exit "${EINVAL}"; }
is_uint "${WALL}" || { note "--wall 需为正整数: ${WALL}"; exit "${EINVAL}"; }
[ -f "${SRC}/arch/x86/boot/bzImage" ] ||
    die "缺少 bzImage，先跑 kernel-refbuild.sh"

# 未显式指定时按 /dev/kvm 可读写与否自适应；托管 runner 通常无此设备。
if [ -z "${ACCEL_SET}" ] && { [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; }; then
    ACCEL="--disable-kvm"
fi
if [ -z "${ACCEL}" ] && { [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; }; then
    die "要求 KVM 但 /dev/kvm 不可读写（去掉 --kvm 或改用 --no-kvm）"
fi

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

# install 目录是「构建」与「运行」的唯一契约。某个集合编译失败（例如缺
# libcap-dev 让 seccomp 拿不到 sys/capability.h）时，install 只跳过该目录并
# 打印一行 Skipping，清单里就少掉整个集合，而 run 段照样「全部通过」。
# 逐个断言目标确有用例，把静默丢弃变成硬失败。
group "校验 kselftest 目标均已产出用例"
missing=""
for target in ${TARGETS}; do
    grep -q "^${target}:" "${INSTALL}/kselftest-list.txt" ||
        missing="${missing} ${target}"
done
[ -z "${missing}" ] || die "目标未产出用例:${missing}（该集合构建失败或缺依赖）"
endgroup

group "virtme-ng 运行 kselftest（OS-TEST-013~022）"
if [ -n "${ACCEL}" ]; then
    note "加速: TCG；单用例超时 ${TIMEOUT}s，整批上界 ${WALL}s"
else
    note "加速: KVM；单用例超时 ${TIMEOUT}s，整批上界 ${WALL}s"
fi
# vng 的 --exec 收取单个字符串，参数是否经 shell 二次解析取决于版本。
# 把带参数的命令固化成一个可执行入口，两种解析下都成立。
cat >"${INSTALL}/ksft-run.sh" <<EOF
#!/bin/sh
exec ./run_kselftest.sh -o ${TIMEOUT}
EOF
chmod +x "${INSTALL}/ksft-run.sh"
(
    # run_kselftest.sh 由自身位置推导 BASE_DIR，必须从 install 目录运行。
    cd "${INSTALL}" || exit 1
    # 不要加 --summary：它会把 logfile 指向 $BASE_DIR/output.log，而 vng 把
    # install 目录只读挂载，整批用例因此写日志失败、一律 exit=2（0.0.3 实测
    # 19/19）。缺省 logfile 是 /dev/stdout，TAP 直接进 tee，正是解析所需。
    # shellcheck disable=SC2086  # ACCEL 可能为空
    timeout --kill-after=30 "${WALL}" \
        vng --run "${SRC}/arch/x86/boot/bzImage" \
        --cwd "${INSTALL}" --force-9p ${ACCEL} --quiet \
        --exec "./ksft-run.sh"
) >"${TAP}" 2>&1
rc=$?
endgroup
if [ "${rc}" -eq 124 ] || [ "${rc}" -eq 137 ]; then
    tail -n 60 "${TAP}" >&2
    die "kselftest 整批墙钟超限（${WALL}s, rc=${rc}）：疑似死锁，检查最后输出"
fi
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
