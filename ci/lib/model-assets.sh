#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/model-assets.sh — 模型资产门禁的临时离线包构造
#
# 目的：llm-offline 与 model-manifest 都要造一份「引擎 + 资产 + 清单」的
# 临时离线包来跑 install / verify 的正负例。此前 make_bundle 在两个门禁里
# 逐字各一份（26 行）：包的字段只改一处时，两个门禁验的就不是同一个契约，
# 而清单里的 size / sha256 正是资产通道的全部判据。
#
# 调用方：ci/gates/{llm-offline,model-manifest}.sh；source 前 TMP 须指向已
# 建好的临时目录。本文件只写 ${TMP}/engine 与 ${TMP}/bundle，不安装、不
# 清理——清理由 check.sh 的 cleanup 统一负责。
# ===========================================================================

# make_bundle —— 造最小可安装包：引擎带可执行位，清单带真实 size 与 sha256。
make_bundle()
{
    local engine="${TMP}/engine/llama-server"
    local asset="${TMP}/bundle/models/w.gguf"
    local size sha

    mkdir -p "${TMP}/engine" "${TMP}/bundle/models"
    printf '#!/bin/sh\nexit 0\n' >"${engine}"
    chmod 0755 "${engine}"
    printf 'b4000\n' >"${TMP}/engine/ENGINE_VERSION"
    printf 'fake-weights\n' >"${asset}"
    size="$(wc -c <"${asset}")"
    sha="$(sha256sum "${asset}" | cut -d' ' -f1)"
    cat >"${TMP}/bundle/manifest.json" <<EOF
{
  "schema": 1,
  "name": "airy-gate",
  "version": "0.0.1",
  "quant": "q4_k_m",
  "runtime": {
    "engine": "llama.cpp",
    "version": "b4000",
    "path": "${engine}",
    "backends": ["cpu"]
  },
  "assets": [
    {"path": "models/w.gguf", "size": ${size}, "sha256": "${sha}"}
  ]
}
EOF
}
