# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/__init__.py — ci 共享 python 模块的包标记
#
# python 以脚本方式运行时把脚本所在目录（即 ci/）放在 sys.path[0]，所以
# ci/ 下的脚本写 `from lib import rs_src, scan_src` 就能取到共享实现。此前
# 四个脚本各自写一遍 `sys.path.insert(…/lib)` 再 import，那几行逐行相同，
# 会被质量棘轮判成新增重复片段——引导逻辑本身不该是复制品。
# ===========================================================================
"""Shared helpers for the ci tooling."""
