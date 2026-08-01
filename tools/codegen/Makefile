# SPDX-License-Identifier: GPL-2.0
#
# Makefile — agentrt-linux codegen 管道入口
#
# 目标:
#   all     — 运行所有生成器
#   syscall — 运行 syscall_gen.py 生成 C UAPI 头文件
#   check   — 运行所有生成器的 --check 模式（CI 集成用）
#   clean   — 清理生成文件
#
# Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

PYTHON      := python3
CODEGEN_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SYSCALL_GEN := $(CODEGEN_DIR)syscall_gen.py

# 生成产物路径（相对于 agentrt-linux/ 仓库根）
# CODEGEN_DIR 为 .../agentrt-linux/tools/codegen/，需上两级到仓库根
REPO_ROOT   := $(abspath $(CODEGEN_DIR)/../../)
SYSCALL_H   := $(REPO_ROOT)/kernel/include/uapi/linux/airymax/syscall.h

# 生成的文件列表（clean 目标使用）
GENERATED_FILES := $(SYSCALL_H)

.PHONY: all syscall check clean

# all: 运行所有生成器
all: syscall
	@echo "  CODEGEN  all generators completed"

# syscall: 运行 syscall_gen.py 生成 C UAPI 头文件
syscall:
	@echo "  CODEGEN  syscall_gen.py → syscall.h"
	@$(PYTHON) $(SYSCALL_GEN)

# check: 运行所有生成器的 --check 模式（CI 集成用）
check:
	@echo "  CODEGEN  checking generated files match committed files"
	@$(PYTHON) $(SYSCALL_GEN) --check

# clean: 清理生成文件
clean:
	@echo "  CODEGEN  cleaning generated files"
	@rm -f $(GENERATED_FILES)
	@echo "  CODEGEN  cleaned: $(GENERATED_FILES)"
