#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/iso-checksum.sh — artifact 内 ISO 的落盘校验
#
# 用法: ci/gates/iso-checksum.sh --dir DIR [--env-key AIRYMAX_ISO]
#
# artifact 从上传到下载会经过打包与再落盘，摘要必须在使用前重算一次：
# 上游 assemble 已经算过一遍，但那是另一台 runner、另一份文件。此处失败
# 意味着传输或存储损坏，继续装机只会得到一个无法解释的装机失败。
#
# 退出码: 0 通过；1 缺文件或摘要不符；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=iso-checksum
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

DIR=""
ENV_KEY="AIRYMAX_ISO"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)     DIR="${2:-}"; shift 2 ;;
        --env-key) ENV_KEY="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/iso-checksum.sh --dir DIR [--env-key KEY]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }
[ -d "${DIR}" ] || die "目录不存在: ${DIR}"

iso="$(find "${DIR}" -name '*.iso' -type f | head -n 1)"
[ -n "${iso}" ] || die "artifact 中缺少 ISO"
sum="${iso}.sha256"
[ -f "${sum}" ] || die "缺少 ISO 校验和: ${sum}"

( cd "$(dirname "${iso}")" && sha256sum -c "$(basename "${iso}").sha256" ) ||
    die "ISO 摘要校验失败: ${iso}"

env_set "${ENV_KEY}" "${iso}"
say "ISO 就绪: ${iso}"
