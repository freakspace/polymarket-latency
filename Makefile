SHELL := /bin/bash

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard venv/bin/python),venv/bin/python,python3))
BENCHMARK_SCRIPT := ws_benchmark/benchmark.py
REPORT_SCRIPT := ws_benchmark/html_generator.py
REPORTS_DIR := recordings/ws-bench
ORDER_BURST_SCRIPT := polymarket_order_burst.py
ORDER_BURST_REPORT_SCRIPT := order_burst_html_generator.py
ORDER_BURST_DIR := recordings/order-burst

.PHONY: help benchmark benchmark-48h benchmark-tmux bench-sweep report order-burst order-burst-report server web web-dev

help:
	@echo "Available targets:"
	@echo "  make benchmark"
	@echo "    Run ws_benchmark/benchmark.py using the default config flow."
	@echo "    Env overrides: MARKET=<slug> SERIES_ID=<id> TOKEN_ID=<id> DURATION=<s>"
	@echo "                   TOPOLOGIES=1,2,5,10 WARMUP_SECONDS=<s>"
	@echo "                   CONNECTION_ROTATE_SECONDS=<s> WRITE_VISUALS=1 VERBOSE=1"
	@echo "    Use ARGS=\"...\" to pass raw flags (space-separated)."
	@echo ""
	@echo "  make benchmark-48h"
	@echo "    Run the 48-hour latency study using ws_benchmark/benchmark_config_48h.toml"
	@echo "    (topologies 1,2,5,10,15; series 10684; rotation 10s). Tees stdout+stderr to"
	@echo "    LOG (default /tmp/poly-48h.log). Sets BENCHMARK_ASYNCIO_DEBUG=1 and"
	@echo "    BENCHMARK_SLOW_CALLBACK_MS=200 by default. Run inside tmux/screen so"
	@echo "    the SSH session can drop without killing the run."
	@echo "    Env overrides: CONFIG=<path> LOG=<path> ARGS=\"...\" SKIP_DEBUG=1"
	@echo ""
	@echo "  make benchmark-tmux"
	@echo "    Multi-process variant: launches one tmux session per topology so each"
	@echo "    runs in its own Python process (escapes the single-asyncio-loop"
	@echo "    throughput ceiling). Waits for all sessions to finish, then merges"
	@echo "    per-topology summaries into a unified summary.json + report.html under"
	@echo "    recordings/ws-bench-multi/<timestamp>/."
	@echo "    Env overrides: CONFIG=<path> DURATION=<s> TOPOLOGIES=1,2,5,10,15"
	@echo "                   POLL_INTERVAL=<s> SESSION_PREFIX=poly-bench"
	@echo ""
	@echo "  make bench-sweep"
	@echo "    Run the benchmark three times back-to-back on the same market with"
	@echo "    zero warmup and different rotation intervals (none, 60s, 10s), so"
	@echo "    you can compare Freshness P95 and the Drift chart across connection"
	@echo "    lifetimes. Outputs grouped under recordings/ws-bench-sweeps/<timestamp>/."
	@echo "    Env: MARKET=<slug> DURATION=<s> (default 1800) ROTATIONS=\"none,60,10\""
	@echo ""
	@echo "  make report"
	@echo "    List available runs under $(REPORTS_DIR) and $(ORDER_BURST_DIR) and"
	@echo "    prompt you to choose one. Dispatches to the correct renderer based on path."
	@echo ""
	@echo "  make report SUMMARY=<path>/summary.json"
	@echo "    Render a specific summary without the interactive prompt."
	@echo ""
	@echo "  make order-burst TOKEN_ID=<token-id>"
	@echo "    Run the Polymarket CLOB v2 duplicate/latency burst test."
	@echo "    Writes $(ORDER_BURST_DIR)/<timestamp>/summary.json by default."
	@echo "    Optional: REPEATS=10 BURST_MODE=exact-duplicate COUNTS=1,2,5,10"
	@echo ""
	@echo "  make order-burst-report"
	@echo "    List available order-burst runs under $(ORDER_BURST_DIR) and prompt you to choose one."
	@echo ""
	@echo "  make order-burst-report SUMMARY=$(ORDER_BURST_DIR)/<timestamp>/summary.json"
	@echo "    Render a standalone report.html next to the chosen summary.json."
	@echo ""
	@echo "  make server"
	@echo "    List rendered reports from both $(REPORTS_DIR) and $(ORDER_BURST_DIR),"
	@echo "    prompt you to choose one, and serve that directory via 'python3 -m http.server'"
	@echo "    on PORT (default 8000). Prints the URL to open. Override with PORT=... HOST=0.0.0.0."
	@echo ""
	@echo "  make web"
	@echo "    Build and serve the Next.js report dashboard on http://0.0.0.0:3000"
	@echo "    (runs 'next build' then 'next start' in reports/; installs deps if missing)."
	@echo ""
	@echo "  make web-dev"
	@echo "    Start the Next.js dev server with HMR on http://localhost:3000."

benchmark:
	@set -euo pipefail; \
	args=(); \
	if [[ -n "$${MARKET:-}" ]]; then args+=(--market "$$MARKET"); fi; \
	if [[ -n "$${SERIES_ID:-}" ]]; then args+=(--series-id "$$SERIES_ID"); fi; \
	if [[ -n "$${TOKEN_ID:-}" ]]; then args+=(--token-id "$$TOKEN_ID"); fi; \
	if [[ -n "$${DURATION:-}" ]]; then args+=(--duration "$$DURATION"); fi; \
	if [[ -n "$${TOPOLOGIES:-}" ]]; then args+=(--topologies "$$TOPOLOGIES"); fi; \
	if [[ -n "$${WARMUP_SECONDS:-}" ]]; then args+=(--warmup-seconds "$$WARMUP_SECONDS"); fi; \
	if [[ -n "$${WARMUP_COMPARE_WINDOW_SECONDS:-}" ]]; then args+=(--warmup-compare-window-seconds "$$WARMUP_COMPARE_WINDOW_SECONDS"); fi; \
	if [[ -n "$${CONNECTION_ROTATE_SECONDS:-}" ]]; then args+=(--connection-rotate-seconds "$$CONNECTION_ROTATE_SECONDS"); fi; \
	if [[ "$${WRITE_VISUALS:-}" == "1" ]]; then args+=(--write-visuals); fi; \
	if [[ "$${WRITE_EVENT_LOG:-}" == "1" ]]; then args+=(--write-event-log); fi; \
	if [[ "$${WRITE_CONNECTION_LOG:-}" == "1" ]]; then args+=(--write-connection-log); fi; \
	if [[ "$${VERBOSE:-}" == "1" ]]; then args+=(--verbose); fi; \
	if [[ -n "$${ARGS:-}" ]]; then \
		eval "extra_args=($$ARGS)"; \
		args+=("$${extra_args[@]}"); \
	fi; \
	echo "[make] running benchmark via $(BENCHMARK_SCRIPT) $${args[*]:-}"; \
	exec "$(PYTHON)" "$(BENCHMARK_SCRIPT)" $${args[@]+"$${args[@]}"}

benchmark-48h:
	@set -euo pipefail; \
	config="$${CONFIG:-ws_benchmark/benchmark_config_48h.toml}"; \
	log="$${LOG:-/tmp/poly-48h.log}"; \
	if [[ ! -f "$$config" ]]; then \
		echo "[make] config not found: $$config"; \
		exit 1; \
	fi; \
	args=(--config "$$config"); \
	if [[ -n "$${ARGS:-}" ]]; then \
		eval "extra_args=($$ARGS)"; \
		args+=("$${extra_args[@]}"); \
	fi; \
	if [[ -z "$${TMUX:-}" && -z "$${STY:-}" ]]; then \
		echo "[make] WARNING: not inside tmux/screen — SSH disconnect will kill this 48h run."; \
		echo "[make]   tmux new -s polymarket  (then re-run this target)"; \
		echo ""; \
	fi; \
	if [[ "$${SKIP_DEBUG:-0}" != "1" ]]; then \
		export BENCHMARK_ASYNCIO_DEBUG="$${BENCHMARK_ASYNCIO_DEBUG:-1}"; \
		export BENCHMARK_SLOW_CALLBACK_MS="$${BENCHMARK_SLOW_CALLBACK_MS:-200}"; \
	fi; \
	echo "[make] config: $$config"; \
	echo "[make] log:    $$log"; \
	echo "[make] env:    BENCHMARK_ASYNCIO_DEBUG=$${BENCHMARK_ASYNCIO_DEBUG:-unset} BENCHMARK_SLOW_CALLBACK_MS=$${BENCHMARK_SLOW_CALLBACK_MS:-unset}"; \
	echo "[make] cmd:    $(PYTHON) $(BENCHMARK_SCRIPT) $${args[*]}"; \
	echo ""; \
	"$(PYTHON)" "$(BENCHMARK_SCRIPT)" "$${args[@]}" 2>&1 | tee "$$log"

benchmark-tmux:
	@set -euo pipefail; \
	config="$${CONFIG:-ws_benchmark/benchmark_config_48h.toml}"; \
	if [[ ! -f "$$config" ]]; then \
		echo "[make] config not found: $$config"; \
		exit 1; \
	fi; \
	export PYTHON="$(PYTHON)"; \
	exec bash ws_benchmark/bench_multi.sh "$$config"

bench-sweep:
	@set -euo pipefail; \
	sweep_ts="$$(date +%Y%m%d_%H%M%S)"; \
	root="recordings/ws-bench-sweeps/$$sweep_ts"; \
	mkdir -p "$$root"; \
	duration="$${DURATION:-1800}"; \
	market="$${MARKET:-}"; \
	series_id="$${SERIES_ID:-}"; \
	rotations_spec="$${ROTATIONS:-none,60,10}"; \
	IFS=',' read -r -a rotations <<< "$$rotations_spec"; \
	echo "[sweep] root=$$root duration=$${duration}s rotations=$$rotations_spec"; \
	if [[ -n "$$market" ]]; then echo "[sweep] market=$$market"; fi; \
	if [[ -n "$$series_id" ]]; then echo "[sweep] series_id=$$series_id"; fi; \
	started_epoch=$$(date +%s); \
	for rotation in "$${rotations[@]}"; do \
		label="$$rotation"; \
		out_dir="$$root/rotation_$$label"; \
		echo ""; \
		echo "[sweep] ============================================================"; \
		echo "[sweep]  run: rotation=$$label  →  $$out_dir"; \
		echo "[sweep] ============================================================"; \
		args=(--duration "$$duration" --warmup-seconds 0 --write-visuals --output-dir "$$out_dir"); \
		if [[ -n "$$market" ]]; then args+=(--market "$$market"); fi; \
		if [[ -n "$$series_id" ]]; then args+=(--series-id "$$series_id"); fi; \
		if [[ "$$label" != "none" ]]; then args+=(--connection-rotate-seconds "$$label"); fi; \
		run_started=$$(date +%s); \
		"$(PYTHON)" "$(BENCHMARK_SCRIPT)" "$${args[@]}"; \
		run_elapsed=$$(( $$(date +%s) - run_started )); \
		echo "[sweep] run rotation=$$label finished in $${run_elapsed}s"; \
	done; \
	total_elapsed=$$(( $$(date +%s) - started_epoch )); \
	echo ""; \
	echo "[sweep] ============================================================"; \
	echo "[sweep] sweep complete in $${total_elapsed}s"; \
	echo "[sweep] reports:"; \
	for rotation in "$${rotations[@]}"; do \
		echo "  rotation=$$rotation  →  $$root/rotation_$$rotation/report.html"; \
	done

report:
	@set -euo pipefail; \
	summary="$${SUMMARY:-}"; \
	if [[ -z "$$summary" ]]; then \
		summaries=(); \
		while IFS= read -r line; do \
			[[ -n "$$line" ]] && summaries+=("$$line"); \
		done < <({ \
			find "$(REPORTS_DIR)" -type f -name summary.json 2>/dev/null; \
			find "$(ORDER_BURST_DIR)" -type f -name summary.json 2>/dev/null; \
		} | awk -F/ '{print $$(NF-1)"\t"$$0}' | sort -r | cut -f2-); \
		if (( $${#summaries[@]} == 0 )); then \
			echo "[make] no benchmark summaries found under $(REPORTS_DIR) or $(ORDER_BURST_DIR)"; \
			echo "[make] run 'make benchmark' or 'make order-burst' first, or pass SUMMARY=..."; \
			exit 1; \
		fi; \
		echo "Available benchmark runs:"; \
		for i in "$${!summaries[@]}"; do \
			run_dir="$$(dirname "$${summaries[$$i]}")"; \
			case "$$run_dir" in \
				$(ORDER_BURST_DIR)*) tag="order-burst";; \
				$(REPORTS_DIR)*) tag="ws-bench";; \
				*) tag="other";; \
			esac; \
			printf "  %2d) [%-10s] %s\n" "$$((i + 1))" "$$tag" "$$run_dir"; \
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
	case "$$summary" in \
		$(ORDER_BURST_DIR)/*) script="$(ORDER_BURST_REPORT_SCRIPT)"; kind="order-burst";; \
		*) script="$(REPORT_SCRIPT)"; kind="ws-bench";; \
	esac; \
	echo "[make] rendering $$kind report for $$summary"; \
	"$(PYTHON)" "$$script" "$$summary"

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

order-burst-report:
	@set -euo pipefail; \
	summary="$${SUMMARY:-}"; \
	if [[ -z "$$summary" ]]; then \
		summaries=(); \
		while IFS= read -r line; do \
			[[ -n "$$line" ]] && summaries+=("$$line"); \
		done < <(find "$(ORDER_BURST_DIR)" -type f -name summary.json 2>/dev/null | sort -r); \
		if (( $${#summaries[@]} == 0 )); then \
			echo "[make] no order-burst summaries found under $(ORDER_BURST_DIR)"; \
			echo "[make] run 'make order-burst TOKEN_ID=...' first or pass SUMMARY=..."; \
			exit 1; \
		fi; \
		echo "Available order-burst runs:"; \
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
	echo "[make] rendering order-burst report for $$summary"; \
	"$(PYTHON)" "$(ORDER_BURST_REPORT_SCRIPT)" "$$summary"

server:
	@set -euo pipefail; \
	port="$${PORT:-8000}"; \
	host="$${HOST:-0.0.0.0}"; \
	run_dir="$${RUN_DIR:-}"; \
	if [[ -z "$$run_dir" ]]; then \
		runs=(); \
		while IFS= read -r line; do \
			[[ -n "$$line" ]] && runs+=("$$line"); \
		done < <({ \
			find "$(REPORTS_DIR)" -type f -name report.html 2>/dev/null; \
			find "$(ORDER_BURST_DIR)" -type f -name report.html 2>/dev/null; \
			find "recordings/ws-bench-sweeps" -type f -name report.html 2>/dev/null; \
		} | awk -F/ '{print $$(NF-1)"\t"$$0}' | sort -r | cut -f2-); \
		if (( $${#runs[@]} == 0 )); then \
			echo "[make] no rendered reports found under $(REPORTS_DIR), $(ORDER_BURST_DIR), or recordings/ws-bench-sweeps"; \
			echo "[make] run 'make report', 'make order-burst-report', or 'make bench-sweep' first to render report.html"; \
			exit 1; \
		fi; \
		echo "Available reports:"; \
		for i in "$${!runs[@]}"; do \
			rd="$$(dirname "$${runs[$$i]}")"; \
			case "$$rd" in \
				$(ORDER_BURST_DIR)*) tag="order-burst";; \
				recordings/ws-bench-sweeps*) tag="ws-sweep";; \
				$(REPORTS_DIR)*) tag="ws-bench";; \
				*) tag="other";; \
			esac; \
			printf "  %2d) [%-10s] %s\n" "$$((i + 1))" "$$tag" "$$rd"; \
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
