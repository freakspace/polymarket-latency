SHELL := /bin/bash

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard venv/bin/python),venv/bin/python,python3))
BENCHMARK_SCRIPT := ws_benchmark/benchmark.py
REPORT_SCRIPT := ws_benchmark/html_generator.py
REPORTS_DIR := recordings/ws-bench
ORDER_BURST_SCRIPT := polymarket_order_burst.py
ORDER_BURST_DIR := recordings/order-burst

.PHONY: help benchmark report order-burst server web web-dev

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
	@echo "  make server"
	@echo "    List runs with a rendered report.html, prompt you to choose one,"
	@echo "    and serve that directory via 'python3 -m http.server' on PORT (default 8000)."
	@echo "    Prints the URL to open. Override with PORT=... HOST=0.0.0.0."
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

server:
	@set -euo pipefail; \
	port="$${PORT:-8000}"; \
	host="$${HOST:-0.0.0.0}"; \
	run_dir="$${RUN_DIR:-}"; \
	if [[ -z "$$run_dir" ]]; then \
		runs=(); \
		while IFS= read -r line; do \
			[[ -n "$$line" ]] && runs+=("$$line"); \
		done < <(find "$(REPORTS_DIR)" -type f -name report.html 2>/dev/null | sort -r); \
		if (( $${#runs[@]} == 0 )); then \
			echo "[make] no rendered reports found under $(REPORTS_DIR)"; \
			echo "[make] run 'make report' first to render report.html"; \
			exit 1; \
		fi; \
		echo "Available reports:"; \
		for i in "$${!runs[@]}"; do \
			printf "  %2d) %s\n" "$$((i + 1))" "$$(dirname "$${runs[$$i]}")"; \
		done; \
		echo ""; \
		read -r -p "Choose a report to serve [1-$${#runs[@]}]: " choice; \
		if [[ ! "$$choice" =~ ^[0-9]+$$ ]]; then \
			echo "[make] invalid selection: $$choice"; \
			exit 1; \
		fi; \
		if (( choice < 1 || choice > $${#runs[@]} )); then \
			echo "[make] selection out of range: $$choice"; \
			exit 1; \
		fi; \
		run_dir="$$(dirname "$${runs[$$((choice - 1))]}")"; \
	else \
		if [[ ! -f "$$run_dir/report.html" ]]; then \
			echo "[make] $$run_dir/report.html not found"; \
			exit 1; \
		fi; \
	fi; \
	if [[ -n "$${URL_HOST:-}" ]]; then \
		ip="$$URL_HOST"; \
	else \
		ip="$$(curl -fsS -m 1 -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' -X PUT http://169.254.169.254/latest/api/token 2>/dev/null \
			| { read -r token; [[ -n "$$token" ]] && curl -fsS -m 1 -H "X-aws-ec2-metadata-token: $$token" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null; } \
			|| curl -fsS -m 2 https://api.ipify.org 2>/dev/null \
			|| curl -fsS -m 2 https://ifconfig.me 2>/dev/null \
			|| hostname -I 2>/dev/null | awk '{print $$1}' \
			|| echo "$$host")"; \
	fi; \
	[[ -z "$$ip" ]] && ip="$$host"; \
	echo ""; \
	echo "[make] serving $$run_dir"; \
	echo "[make] open: http://$$ip:$$port/report.html"; \
	echo "[make] bound to $$host:$$port (Ctrl-C to stop)"; \
	echo ""; \
	cd "$$run_dir" && exec python3 -m http.server "$$port" --bind "$$host"

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
