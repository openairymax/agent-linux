#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/kernel-kselftest.sh — kselftest 子集构建、运行与 TAP 解析
#
# 用法:
#   ci/gates/kernel-kselftest.sh [--src DIR] [--install DIR] [--tap FILE]
#                               [--targets "seccomp pidfd membarrier rseq"]
#                               [--skip "rseq:run_param_test.sh"]
#                               [--wall SEC] [--kvm|--no-kvm]
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
# 超时预算必须按「模拟环境」而不是「裸机」设定，而且必须按目录分档。
# 上游预算有两层语义：
#   1. 裸机缺省 kselftest_default_timeout=45；
#   2. 目录可用 settings 文件改预算，timeout=0 表示「无界」。
# 早先用 run_kselftest.sh -o 做全局覆盖，把这两层一起抹平，问题是可
# 预测的：
#   - pidfd_test 内含多段 sleep(3) 与自旋等待，TCG 下必然击穿 45s，表现
#     为 not ok ... # TIMEOUT 45 seconds；因为 stdio 全缓冲被 SIGKILL
#     截断，TAP 里零用例输出，无法据此区分死锁与慢。
#   - rseq/settings 声明 timeout=0（上游把该目录当 soak 用），全局 -o 把
#     它改写成 240s，压力脚本 run_param_test.sh 永远跑不完。失败项于是
#     从 pidfd 平移到 rseq —— 全局覆盖只是把预算矛盾换了个落点，没有
#     解决「谁该有多少预算」。
# 现在按目录把预算写进安装目录的 settings，上游已声明有限预算的目录
# 一律尊重（例如 seccomp 的 120s），不再做全局覆盖。
#
# 浸泡类用例在有界 CI 里无解，只能显式剔除并登记：rseq/run_param_test.sh
# 一轮 = 默认 8 组配置 + 9 个注入点 + 9 个阻塞注入相位，每组配置跑 4 个
# 二进制、1000 reps、6×ncpu 线程，合计约 608 次进程启动。它不是回归
# 信号，属于实机浸泡，登记在 0.0.4 的实机验证任务里。
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
# 剔除项（dir:name，空格分隔）。剔除本身是可审计的：名字对不上就硬失败。
SKIP="${KSFT_SKIP:-rseq:run_param_test.sh}"
# 整批墙钟上界（秒），防止死锁把 job 拖到 job 级超时。
WALL="${KSFT_WALL:-1200}"
ACCEL=""
ACCEL_SET=""

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-kselftest.sh [--src DIR] [--install DIR] [--tap FILE]
                                   [--targets "seccomp pidfd membarrier rseq"]
                                   [--skip "rseq:run_param_test.sh"]
                                   [--wall SEC] [--kvm|--no-kvm]

  --install  构建安装目录（缺省 $KSFT_INSTALL 或 <repo>/kselftest-install）
  --skip     从清单剔除的用例（dir:name，空格分隔；空串表示不剔除）
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

# budget DIR —— 目录级单用例预算（秒）。只列上游未声明或声明为无界的
# 目录；上游已声明有限预算的目录不在此覆盖。
budget()
{
    case "$1" in
    # 多段 sleep(3) 与自旋等待，累计耗时会击穿裸机口径的 45s。
    pidfd) echo 240 ;;
    # 多轮 barrier 语义验证，轮次不随 CPU 数缩减。
    membarrier) echo 240 ;;
    # 上游 timeout=0 把整个目录当 soak；剔除压力脚本后剩下的都是秒级
    # 二进制，给一个有界预算即可，避免无界用例吃掉整批墙钟。
    rseq) echo 240 ;;
    *) echo "" ;;
    esac
}

# upstream_budget DIR —— 上游 settings 声明的有限预算；无声明或声明为 0
# （无界）时输出空串。0 在有界 CI 里等价于「没有预算」，不能继承。
upstream_budget()
{
    local file="${SRC}/tools/testing/selftests/$1/settings"
    local value=""

    [ -r "${file}" ] || return 0
    value="$(sed -n 's/^timeout=//p' "${file}" | tail -n 1)"
    case "${value}" in
    '' | 0) return 0 ;;
    esac
    echo "${value}"
}

# runner 在每次运行前重置 kselftest_timeout 再从 $BASE_DIR/<dir>/settings
# 读取，所以预算必须落在安装目录，不能改源码树：改源码树等于改上游，
# 而且下一次 install 会把它覆盖回去。
apply_budget()
{
    local target secs upstream

    for target in ${TARGETS}; do
        upstream="$(upstream_budget "${target}")"
        if [ -n "${upstream}" ]; then
            note "${target}: 沿用上游 settings（${upstream}s）"
            continue
        fi
        secs="$(budget "${target}")"
        [ -n "${secs}" ] || continue
        [ -d "${INSTALL}/${target}" ] || die "安装目录缺少 ${target}"
        printf 'timeout=%s\n' "${secs}" >"${INSTALL}/${target}/settings"
        note "${target}: 预算 ${secs}s"
    done
}

# 剔除浸泡脚本。先断言它在清单里，再逐行删除：名字写错时硬失败，而不是
# 静默少跑一批用例（与目标断言同一原则）。
drop_skips()
{
    local list="${INSTALL}/kselftest-list.txt"
    local work="${list}.tmp"
    local case_name

    [ -n "${SKIP}" ] || return 0
    cp "${list}" "${work}"
    for case_name in ${SKIP}; do
        grep -qxF "${case_name}" "${work}" ||
            die "要剔除的用例不在清单内: ${case_name}"
        grep -vxF "${case_name}" "${work}" >"${work}.next" || true
        mv "${work}.next" "${work}"
        note "剔除: ${case_name}"
    done
    mv "${work}" "${list}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -h | --help) usage; exit 0 ;;
    --src) SRC="${2:-}"; shift 2 ;;
    --install) INSTALL="${2:-}"; shift 2 ;;
    --tap) TAP="${2:-}"; shift 2 ;;
    --targets) TARGETS="${2:-}"; shift 2 ;;
    --skip) SKIP="${2:-}"; shift 2 ;;
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

# TAP 路径在此固化：门禁随后会 cd 进内核源码树，相对路径会把产物写进
# 源码树，既污染工作区，也让「产物在哪个目录」变得依赖调用点。
case "${TAP}" in
/*) ;;
*) TAP="${PWD}/${TAP}" ;;
esac

[ -d "${SRC}" ] || die "内核源码目录不存在: ${SRC}（子模块可能未初始化）"
[ -n "${TARGETS}" ] || { note "--targets 不能为空"; exit "${EINVAL}"; }
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

group "落预算并剔除浸泡用例"
apply_budget
drop_skips
note "参与运行: $(wc -l <"${INSTALL}/kselftest-list.txt") 个用例"
endgroup

group "virtme-ng 运行 kselftest（OS-TEST-013~022）"
if [ -n "${ACCEL}" ]; then
    note "加速: TCG；整批上界 ${WALL}s"
else
    note "加速: KVM；整批上界 ${WALL}s"
fi
# vng 的 --exec 收取单个字符串，参数是否经 shell 二次解析取决于版本。
# 预算已按目录写进 settings，这里不需要也没有参数要带。
cat >"${INSTALL}/ksft-run.sh" <<'EOF'
#!/bin/sh
exec ./run_kselftest.sh
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

say "kselftest 通过（${TARGETS}；剔除 ${SKIP:-无}）"
