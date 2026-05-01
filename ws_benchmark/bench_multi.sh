#!/usr/bin/env bash
# Multi-process benchmark runner: spawns one tmux session per topology,
# waits for all to finish, then merges per-process summaries into a
# unified report. Works around the single-asyncio-loop throughput ceiling
# by giving each topology its own Python process.
#
# Usage:
#   ws_benchmark/bench_multi.sh [config-path]
#   make benchmark-tmux
#
# Env overrides:
#   DURATION=43200            override config duration (seconds)
#   TOPOLOGIES=1,2,5,10,15    override config topologies (comma-separated)
#   POLL_INTERVAL=30          how often to check for completion (seconds)
#   SESSION_PREFIX=poly-bench tmux session name prefix

set -euo pipefail

PYTHON="${PYTHON:-venv/bin/python}"
CONFIG="${1:-ws_benchmark/benchmark_config_48h.toml}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"
SESSION_PREFIX="${SESSION_PREFIX:-poly-bench}"

if ! command -v tmux >/dev/null 2>&1; then
    echo "[multi] tmux is required but not installed" >&2
    exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
    echo "[multi] config not found: $CONFIG" >&2
    exit 1
fi

if [[ ! -x "$PYTHON" ]] && ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "[multi] python not found at: $PYTHON (override with PYTHON=...)" >&2
    exit 1
fi

# Determine topologies (env wins, then config)
if [[ -n "${TOPOLOGIES:-}" ]]; then
    TOPS_RAW="$TOPOLOGIES"
else
    TOPS_RAW="$("$PYTHON" -c "
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore
with open('$CONFIG', 'rb') as f:
    cfg = tomllib.load(f)
print(','.join(str(t) for t in cfg.get('topologies', [])))
")"
fi

if [[ -z "$TOPS_RAW" ]]; then
    echo "[multi] no topologies determined" >&2
    exit 1
fi

IFS=',' read -ra TOPS <<< "$TOPS_RAW"

TS="$(date +%Y%m%d_%H%M%S)"
OUTPUT_ROOT="recordings/ws-bench-multi/$TS"
mkdir -p "$OUTPUT_ROOT"

echo "[multi] config:     $CONFIG"
echo "[multi] topologies: ${TOPS[*]}"
echo "[multi] output:     $OUTPUT_ROOT"
echo "[multi] python:     $PYTHON"
if [[ -n "${DURATION:-}" ]]; then
    echo "[multi] duration:   ${DURATION}s (override)"
fi
echo ""

declare -a SESSIONS
declare -a OUT_DIRS

for N in "${TOPS[@]}"; do
    SESSION="${SESSION_PREFIX}-${N}ws-${TS}"
    OUT_DIR="$OUTPUT_ROOT/topology-${N}ws"
    mkdir -p "$OUT_DIR"

    LOG="$OUT_DIR/run.log"

    EXTRA=""
    if [[ -n "${DURATION:-}" ]]; then
        EXTRA="--duration=$DURATION"
    fi

    # The trailing `touch .done` lets the parent script detect completion
    # without depending on tmux session lifecycle.
    CMD="$PYTHON ws_benchmark/benchmark.py --config=$CONFIG --topologies=$N --output-dir=$OUT_DIR $EXTRA 2>&1 | tee '$LOG'; touch '$OUT_DIR/.done'"

    tmux new-session -d -s "$SESSION" -c "$(pwd)" "bash -c \"$CMD\""
    SESSIONS+=("$SESSION")
    OUT_DIRS+=("$OUT_DIR")
    echo "[multi] launched $SESSION  ->  $OUT_DIR"
done

echo ""
echo "[multi] $(date '+%H:%M:%S') waiting for ${#SESSIONS[@]} sessions to complete"
echo "[multi]   poll interval: ${POLL_INTERVAL}s"
echo "[multi]   peek progress: tmux attach -t ${SESSIONS[0]}  (Ctrl-B then D to detach)"
echo "[multi]   list sessions: tmux ls"
echo ""

# Wait for every session's .done marker to appear.
ALL_DONE=0
START_WAIT=$(date +%s)
LAST_REPORT=0
while [[ $ALL_DONE -eq 0 ]]; do
    ALL_DONE=1
    PENDING=0
    for OUT_DIR in "${OUT_DIRS[@]}"; do
        if [[ ! -f "$OUT_DIR/.done" ]]; then
            ALL_DONE=0
            PENDING=$((PENDING + 1))
        fi
    done
    if [[ $ALL_DONE -eq 0 ]]; then
        NOW=$(date +%s)
        # Report status every 5 minutes
        if (( NOW - LAST_REPORT >= 300 )); then
            ELAPSED=$((NOW - START_WAIT))
            printf "[multi] %s  pending: %d/%d  elapsed: %ds\n" \
                "$(date '+%H:%M:%S')" "$PENDING" "${#OUT_DIRS[@]}" "$ELAPSED"
            LAST_REPORT=$NOW
        fi
        sleep "$POLL_INTERVAL"
    fi
done

echo "[multi] $(date '+%H:%M:%S') all sessions complete"
echo ""

# Clean up any lingering tmux sessions.
for SESSION in "${SESSIONS[@]}"; do
    tmux kill-session -t "$SESSION" 2>/dev/null || true
done

echo "[multi] merging summaries..."
"$PYTHON" ws_benchmark/merge_summaries.py "$OUTPUT_ROOT"
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
    echo "[multi] merge failed with exit $EXIT_CODE" >&2
    echo "[multi] per-topology summaries are still available under $OUTPUT_ROOT" >&2
    exit $EXIT_CODE
fi

echo ""
echo "[multi] complete"
echo "[multi]   output:  $OUTPUT_ROOT/"
echo "[multi]   summary: $OUTPUT_ROOT/summary.json"
echo "[multi]   report:  $OUTPUT_ROOT/report.html"
