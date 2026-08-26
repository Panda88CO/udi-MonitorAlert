# Python Coding, Testing & Integration Standards

## 1. Runtime Compatibility
- Target environment is **Python 3.10+ on Universal Devices eISY (FreeBSD/Linux)** running under **PG3x**.
- `udi_interface` is provided by the PG3x runtime environment; always provide safe fallback mocks for local test execution.
- Maintain compatibility with standard Python built-ins; avoid unnecessary heavy dependencies.

## 2. In-Database Computation & Performance
- Always compute aggregations, statistical baselines, window calculations, and outlier scans **directly in SQLite** (`history.db`) using native C functions (`AVG`, `SQRT`, `COUNT`, `LAG() OVER (...)`).
- Utilize composite index `(node_id, control, event_time_ms)` to ensure real-time query latency is below 1 ms.
- Avoid pulling large raw datasets into Python memory (`list`, `dict`, `pandas`).

## 3. Anomaly & Outlier Detection Contract
- `ml_engine.analyze_datapoint(...)` evaluates data against sliding-window baselines ($Z \ge 3.0$) and step rate spikes.
- Returns a 3-tuple: `(is_anomaly: bool, score: int, details: dict)`.
- Never raise uncaught exceptions from `ml_engine` or `database` that would disrupt event ingestion in `udiMonitor.py`.

## 4. Testing Standards
- All core logic must have unit tests run via `python -m unittest discover -v`.
- Test suites:
  - `test_anomaly_detection.py`: ML scoring, Z-score math, velocity spikes, edge cases.
  - `test_database.py`: Schema initialization, CRUD, upserts, filter rule evaluation.
  - `test_udimonitor_helpers.py`: Timestamp parsing, IP normalization, profile matching.
  - `test_subscribers.py`: NuCore and IoX event normalization and callback contracts.
  - `test_parse_rest.py`: REST profile XML and NLS enum parsing.
