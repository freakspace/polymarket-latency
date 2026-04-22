# Standalone Polymarket CLOB WS Benchmark

This directory contains a self-contained benchmark script that can run without the Raven repo.

## Files

- `benchmark.py`
- `html_generator.py`
- `benchmark_config.toml`
- `benchmark_config.toml.example`
- `requirements.txt`

## Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

From the repo root you can also use:

```bash
make benchmark
make report
```

`make report` lists the available runs under `recordings/ws-bench` and lets you choose one interactively.

## Run

8-hour summary-only run:

```bash
python benchmark.py --series-id 10684 --duration 28800
```

Using the default `benchmark_config.toml` next to the script:

```bash
python benchmark.py
```

This writes run artifacts under `recordings/ws-bench/<timestamp>` in this repo by default.

Using a specific config file:

```bash
python benchmark.py --config benchmark_config.toml
```

CLI flags override the config file, so for example:

```bash
python benchmark.py --config benchmark_config.toml --duration 3600
```

If you want HTML/SVG output too:

```bash
python benchmark.py --series-id 10684 --duration 3600 --write-visuals
```

If you want raw event logs for one debugging run:

```bash
python benchmark.py --series-id 10684 --duration 900 --write-event-log --write-connection-log
```

Generate an HTML report later from an existing run:

```bash
python html_generator.py ../recordings/ws-bench/<timestamp>/summary.json
```

If `events.jsonl` is next to `summary.json`, the generator will pick it up automatically and include the timeline charts.

## Notes

- Default mode is summary-only.
- No Raven imports are required.
- The benchmark itself only imports `websockets`, but `requirements.txt` now includes the repo root requirements so one install covers both tools.
- Market and series discovery uses the Polymarket Gamma API directly.
- Config files can be `.json` or `.toml`.
