# ===========================================================================
# agentrt-linux (AirymaxOS) Management Repository Makefile
# ===========================================================================
# Authority: docs/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md §2.4.4
# ===========================================================================

.PHONY: codeowners-sync codeowners-check help

help:
	@echo "agentrt-linux management repository targets:"
	@echo "  codeowners-sync    Generate .github/CODEOWNERS from MAINTAINERS"
	@echo "  codeowners-check   Verify CODEOWNERS consistency with MAINTAINERS"

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
