#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/container-load.sh — 载入固定 openEuler 容器镜像并自证身份
#
# 用法: ci/gates/container-load.sh --file TAR [--env-key AIRYMAX_BUILD_IMAGE]
#
# 只载入不校验，会把「镜像不是 openEuler」的问题推迟到容器里 dnf 装包失败
# 才暴露，而那时已经过去几分钟，报错还指向包管理器。此处载入后立刻读
# /etc/os-release 断言 ID=openeuler，失败即停。
#
# 退出码: 0 通过；1 载入失败或镜像身份不符；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

FILE=""
ENV_KEY="AIRYMAX_BUILD_IMAGE"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --file)    FILE="${2:-}"; shift 2 ;;
        --env-key) ENV_KEY="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/container-load.sh --file TAR [--env-key KEY]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${FILE}" ] || { err "--file 必填"; exit "${EINVAL}"; }
[ -f "${FILE}" ] || die "镜像文件不存在: ${FILE}"

need_cmd docker

log="$(mktemp)"
trap 'rm -f "${log}"' EXIT

group "docker load"
docker load --input "${FILE}" | tee "${log}"
endgroup

img="$(sed -n 's/^Loaded image: //p' "${log}" | tail -n 1)"
[ -n "${img}" ] || die "无法解析镜像名: ${FILE}"

docker run --rm "${img}" grep -qi '^ID="\?openeuler"' /etc/os-release ||
    die "容器镜像不是 openEuler: ${img}"

env_set "${ENV_KEY}" "${img}"
say "构建镜像就绪: ${img}"
