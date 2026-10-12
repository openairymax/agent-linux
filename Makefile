# ===========================================================================
# agent-linux (AirymaxOS) Management Repository Makefile
# ===========================================================================
# Authority: docs/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md §2.4.4
# ===========================================================================

.PHONY: codeowners-sync codeowners-check codegen all help

help:
	@echo "agent-linux management repository targets:"
	@echo "  codeowners-sync    Generate .github/CODEOWNERS from MAINTAINERS"
	@echo "  codeowners-check   Verify CODEOWNERS consistency with MAINTAINERS"
	@echo "  codegen            Run the codegen pipeline (contracts -> generated)"
	@echo "  all                Run codegen, then build the kernel submodule"

## codeowners-sync: 从 MAINTAINERS 生成 CODEOWNERS
codeowners-sync:
	@echo "  SYNC    CODEOWNERS from MAINTAINERS"
	@./scripts/airymax/maintainers_to_codeowners.pl \
		MAINTAINERS > CODEOWNERS

## codeowners-check: 验证 CODEOWNERS 与 MAINTAINERS 一致
codeowners-check:
	@echo "  CHECK   CODEOWNERS consistency"
	@if ! diff -q CODEOWNERS \
		<(./scripts/airymax/maintainers_to_codeowners.pl MAINTAINERS) \
		> /dev/null 2>&1; then \
		echo "Error: CODEOWNERS out of sync. Run 'make codeowners-sync'."; \
		exit 1; \
	fi
	@echo "  OK      CODEOWNERS consistent"

# ===========================================================================
# codegen 管道（docs-linux/30-interfaces/06-codegen-pipeline.md §7.1）
# 契约源（syscall.xml / *.bf / airy_cap_ids.def / contracts.md）为唯一输入，
# 生成 [SC] 共享契约层头文件与多语言绑定；生成是编译的前置步骤。
# ===========================================================================

## codegen: 运行全部代码生成器（按依赖序）
codegen:
	@echo "  CODEGEN running codegen pipeline"
	@python3 tools/codegen/run_codegen.py

## all: codegen 先于 kernel 编译（§7.1 串行依赖）
all: codegen
	$(MAKE) -C kernel
