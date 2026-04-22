SHELL := /bin/bash

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard venv/bin/python),venv/bin/python,python3))
BENCHMARK_SCRIPT := ws_benchmark/benchmark.py
REPORT_SCRIPT := ws_benchmark/html_generator.py
REPORTS_DIR := recordings/ws-bench
ORDER_BURST_SCRIPT := polymarket_order_burst.py
ORDER_BURST_DIR := recordings/order-burst

.PHONY: help benchmark report order-burst web web-dev

help:
	@echo "Available targets:"
	@echo "  make benchmark"
	@echo "    Run ws_benchmark/benchmark.py using the default config flow."
	@echo ""
	@echo "  make report"
	@echo "    List available benchmark runs under $(REPORTS_DIR) and prompt you to choose one."
	@echo ""
	@echo "  make report SUMMARY=$(REPORTS_DIR)/<timestamp>/summary.json"
	@echo "    Render a specific summary without the interactive prompt."
	@echo ""
	@echo "  make order-burst TOKEN_ID=<token-id>"
	@echo "    Run the Polymarket CLOB v2 duplicate/latency burst test."
	@echo "    Writes $(ORDER_BURST_DIR)/<timestamp>/summary.json by default."
	@echo "    Optional: REPEATS=10 BURST_MODE=exact-duplicate COUNTS=1,2,5,10"
	@echo ""
	@echo "  make web"
	@echo "    Build and serve the Next.js report dashboard on http://0.0.0.0:3000"
	@echo "    (runs 'next build' then 'next start' in reports/; installs deps if missing)."
	@echo ""
	@echo "  make web-dev"
	@echo "    Start the Next.js dev server with HMR on http://localhost:3000."

benchmark:
	@echo "[make] running benchmark via $(BENCHMARK_SCRIPT)"
	@"$(PYTHON)" "$(BENCHMARK_SCRIPT)"

report:
	@set -euo pipefail; \
	summary="$${SUMMARY:-}"; \
	if [[ -z "$$summary" ]]; then \
		summaries=(); \
		while IFS= read -r line; do \
			[[ -n "$$line" ]] && summaries+=("$$line"); \
		done < <(find "$(REPORTS_DIR)" -type f -name summary.json 2>/dev/null | sort -r); \
		if (( $${#summaries[@]} == 0 )); then \
			echo "[make] no benchmark summaries found under $(REPORTS_DIR)"; \
			echo "[make] run 'make benchmark' first or pass SUMMARY=..."; \
			exit 1; \
		fi; \
		echo "Available benchmark runs:"; \
		for i in "$${!summaries[@]}"; do \
			run_dir="$$(dirname "$${summaries[$$i]}")"; \
			printf "  %2d) %s\n" "$$((i + 1))" "$$run_dir"; \
		done; \
		echo ""; \
		read -r -p "Choose a run to render [1-$${#summaries[@]}]: " choice; \
		if [[ ! "$$choice" =~ ^[0-9]+$$ ]]; then \
			echo "[make] invalid selection: $$choice"; \
			exit 1; \
		fi; \
		if (( choice < 1 || choice > $${#summaries[@]} )); then \
			echo "[make] selection out of range: $$choice"; \
			exit 1; \
		fi; \
		summary="$${summaries[$$((choice - 1))]}"; \
	else \
		if [[ -d "$$summary" ]]; then \
			summary="$${summary%/}/summary.json"; \
		fi; \
		if [[ ! -f "$$summary" ]]; then \
			echo "[make] summary file not found: $$summary"; \
			exit 1; \
		fi; \
	fi; \
	echo "[make] rendering report for $$summary"; \
	"$(PYTHON)" "$(REPORT_SCRIPT)" "$$summary"

order-burst:
	@set -euo pipefail; \
	token_id="$${TOKEN_ID:-}"; \
	if [[ -z "$$token_id" ]]; then \
		echo "[make] TOKEN_ID is required"; \
		echo "[make] example:"; \
		echo "[make]   make order-burst TOKEN_ID=102936224134271070189104847090829839924697394514566827387181305960175107677216"; \
		exit 1; \
	fi; \
	run_dir="$${RUN_DIR:-$(ORDER_BURST_DIR)/$$(date +%Y%m%d_%H%M%S)}"; \
	summary="$$run_dir/summary.json"; \
	mkdir -p "$$run_dir"; \
	args=(--token-id "$$token_id" --json-out "$$summary"); \
	if [[ -n "$${SIDE:-}" ]]; then args+=(--side "$$SIDE"); fi; \
	if [[ -n "$${PRICE:-}" ]]; then args+=(--price "$$PRICE"); fi; \
	if [[ -n "$${SIZE:-}" ]]; then args+=(--size "$$SIZE"); fi; \
	if [[ -n "$${COUNTS:-}" ]]; then args+=(--counts "$$COUNTS"); fi; \
	if [[ -n "$${REPEATS:-}" ]]; then args+=(--repeats "$$REPEATS"); fi; \
	if [[ -n "$${BURST_MODE:-}" ]]; then args+=(--burst-mode "$$BURST_MODE"); fi; \
	if [[ -n "$${HOST:-}" ]]; then args+=(--host "$$HOST"); fi; \
	if [[ -n "$${CHAIN_ID:-}" ]]; then args+=(--chain-id "$$CHAIN_ID"); fi; \
	if [[ -n "$${SETTLE_SECONDS:-}" ]]; then args+=(--settle-seconds "$$SETTLE_SECONDS"); fi; \
	if [[ "$${POST_ONLY:-1}" == "1" ]]; then args+=(--post-only); else args+=(--no-post-only); fi; \
	if [[ "$${CLEANUP:-1}" == "1" ]]; then args+=(--cleanup); else args+=(--no-cleanup); fi; \
	echo "[make] running order burst via $(ORDER_BURST_SCRIPT)"; \
	echo "[make] summary: $$summary"; \
	"$(PYTHON)" "$(ORDER_BURST_SCRIPT)" "$${args[@]}"

web:
	@set -euo pipefail; \
	if [ ! -d reports/node_modules ]; then \
		echo "[make] installing reports/ deps (first run)"; \
		cd reports && npm install --no-fund --no-audit; \
		cd ..; \
	fi; \
	port="$${PORT:-4242}"; \
	host="$${HOST:-127.0.0.1}"; \
	echo "[make] building Next.js app"; \
	cd reports && npx next build; \
	echo "[make] starting Next.js on http://$$host:$$port"; \
	exec npx next start -H "$$host" -p "$$port"

web-dev:
	@if [ ! -d reports/node_modules ]; then \
		echo "[make] installing reports/ deps (first run)"; \
		cd reports && npm install --no-fund --no-audit; \
	fi
	@echo "[make] starting Next.js dev server at http://127.0.0.1:4242"
	@cd reports && npx next dev -H 127.0.0.1 -p 4242
