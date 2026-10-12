# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# Makefile — agent-linux codegen 管道入口
#
# 目标:
#   all      — 按依赖序运行所有生成器（cap → bitfield → ipc → syscall）
#   cap      — 运行 cap_gen.py 生成 capability ID（C 头 + Rust/Python 绑定）
#   bitfield — 运行 bitfield_gen.py 生成位域访问层 C 头
#   ipc      — 运行 ipc_gen.py 生成 A-IPC 消息头绑定（C 头 + Rust/Python）
#   syscall  — 运行 syscall_gen.py 生成 C UAPI 头文件
#   check    — 运行所有生成器的 --check 模式 + syscall_64.tbl 段一致性（CI 集成用）
#   clean    — 清理生成文件
#
# Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

PYTHON      := python3
CODEGEN_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# 生成器入口
CAP_GEN      := $(CODEGEN_DIR)cap_gen.py
BITFIELD_GEN := $(CODEGEN_DIR)bitfield_gen.py
IPC_GEN      := $(CODEGEN_DIR)ipc_gen.py
SYSCALL_GEN  := $(CODEGEN_DIR)syscall_gen.py

# 生成产物路径（相对于 agent-linux/ 仓库根）
# CODEGEN_DIR 为 .../agent-linux/tools/codegen/，需上两级到仓库根
REPO_ROOT   := $(abspath $(CODEGEN_DIR)/../../)

# --- capability ID（cap_gen.py，依赖序最前）---
CAP_C       := $(REPO_ROOT)/kernel/include/uapi/linux/airymax/security_types.h
CAP_RUST    := $(REPO_ROOT)/sdk/rust/airyx/cap_ids.rs
CAP_PY      := $(REPO_ROOT)/sdk/python/airyx/cap_ids.py
CAP_FILES   := $(CAP_C) $(CAP_RUST) $(CAP_PY)

# --- bitfield（bitfield_gen.py，依赖序居中）---
STRUCT64_H  := $(REPO_ROOT)/kernel/include/uapi/linux/airymax/structures_64_gen.h
STRUCT32_H  := $(REPO_ROOT)/kernel/include/uapi/linux/airymax/structures_32_gen.h
BITFIELD_FILES := $(STRUCT64_H) $(STRUCT32_H)

# --- ipc（ipc_gen.py，依赖序居中，与 bitfield 并列）---
IPC_H       := $(REPO_ROOT)/kernel/include/uapi/linux/airymax/ipc.h
IPC_RUST    := $(REPO_ROOT)/sdk/rust/airyx/ipc.rs
IPC_PY      := $(REPO_ROOT)/sdk/python/airyx/ipc.py
IPC_FILES   := $(IPC_H) $(IPC_RUST) $(IPC_PY)

# --- syscall（syscall_gen.py，依赖序最后）---
SYSCALL_H   := $(REPO_ROOT)/kernel/include/uapi/linux/airymax/syscall_gen.h

# 生成的文件列表（clean 目标使用）
GENERATED_FILES := $(CAP_FILES) $(BITFIELD_FILES) $(IPC_FILES) $(SYSCALL_H)

.PHONY: all cap bitfield ipc syscall check clean

# all: 按依赖序运行所有生成器（cap_gen → bitfield_gen → ipc_gen → syscall_gen）
all: cap bitfield ipc syscall
	@echo "  CODEGEN  all generators completed"

# cap: 运行 cap_gen.py 生成 capability ID（C 头 + Rust/Python 绑定）
cap:
	@echo "  CODEGEN  cap_gen.py → security_types.h + cap_ids.{rs,py}"
	@$(PYTHON) $(CAP_GEN)

# bitfield: 运行 bitfield_gen.py 生成位域访问层 C 头
bitfield:
	@echo "  CODEGEN  bitfield_gen.py → structures_{32,64}_gen.h"
	@$(PYTHON) $(BITFIELD_GEN)

# ipc: 运行 ipc_gen.py 生成 A-IPC 消息头绑定（C 头 + Rust/Python）
ipc:
	@echo "  CODEGEN  ipc_gen.py → ipc.h + ipc.{rs,py}"
	@$(PYTHON) $(IPC_GEN)

# syscall: 运行 syscall_gen.py 生成 C UAPI 头文件
syscall:
	@echo "  CODEGEN  syscall_gen.py → syscall_gen.h"
	@$(PYTHON) $(SYSCALL_GEN)

# check: 运行所有生成器的 --check 模式 + syscall_64.tbl 段一致性（CI 集成用）
check:
	@echo "  CODEGEN  checking generated files match committed files"
	@$(PYTHON) $(CAP_GEN) --check
	@$(PYTHON) $(BITFIELD_GEN) --check
	@$(PYTHON) $(IPC_GEN) --check
	@$(PYTHON) $(SYSCALL_GEN) --check
	@$(PYTHON) $(SYSCALL_GEN) --check-tbl

# clean: 清理生成文件
clean:
	@echo "  CODEGEN  cleaning generated files"
	@rm -f $(GENERATED_FILES)
	@echo "  CODEGEN  cleaned: $(GENERATED_FILES)"
