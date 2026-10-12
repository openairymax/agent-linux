# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# Makefile — agent-linux 策略 DSL（.adl）编译器入口
#
# 目标:
#   all      — 编译 tools/dsl/examples/ 下全部 .adl 契约源为纯 C 决策表头
#   check    — 运行 --check 零漂移校验（生成内容与已提交文件零 diff，CI 集成用）
#   test     — 运行 DSL 单元测试套件（pytest，覆盖 lexer/parser/semantics/codegen/compile）
#   clean    — 清理生成文件
#
# Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

PYTHON   := python3
DSL_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# 编译器入口
COMPILE  := $(DSL_DIR)dsl_compile.py

# 生成产物路径（相对于 agent-linux/ 仓库根）
# DSL_DIR 为 .../agent-linux/tools/dsl/，需上两级到仓库根
REPO_ROOT    := $(abspath $(DSL_DIR)/../../)
GENERATED_H  := $(REPO_ROOT)/tools/dsl/examples/security_policy_tables.h
GENERATED_FILES := $(GENERATED_H)

.PHONY: all check test clean

# all: 编译全部 .adl 契约源为纯 C 决策表头
all:
	@echo "  DSL      dsl_compile.py → security_policy_tables.h"
	@$(PYTHON) $(COMPILE)

# check: 零漂移校验（生成内容与已提交文件一致，CI 集成用）
check:
	@echo "  DSL      checking generated headers match committed files"
	@$(PYTHON) $(COMPILE) --check

# test: 运行 DSL 单元测试套件（pytest）
test:
	@echo "  DSL      running pytest unit tests"
	@$(PYTHON) -m pytest $(DSL_DIR)tests/ -q

# clean: 清理生成文件
clean:
	@echo "  DSL      cleaning generated files"
	@rm -f $(GENERATED_FILES)
	@echo "  DSL      cleaned: $(GENERATED_FILES)"
