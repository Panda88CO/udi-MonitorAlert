# Handoff

## Project Goal
Build a PG3x Python node server for Universal Devices eISY that monitors event changes from NuCore/IoX, logs callback events to JSONL and SQLite, and provides in-database statistical outlier and anomaly detection.

## Current State
- Phase 1 and Phase 2 are complete.
- In-database SQLite anomaly and outlier engine is operational and tested.
- NuCore is the preferred event source; IoX WebSocket is the fallback.
- Full automated test suite (30 unit tests) passes cleanly in local and CI environments.

## Completed Work
- Fixed `manifest.json` to valid JSON with `udiMonitor.py` entrypoint.
- Added in-database statistical baselines ($\mu$, $\sigma$) and sliding-window calculations in `database.py`.
- Added $Z$-score and step/velocity spike detection in `ml_engine.py`.
- Wired real-time anomaly evaluation into `udiMonitor.py`.
- Created interactive CLI inspection tool (`import sqlite3.py`).
- Added 30 automated unit tests across 5 test suites (`test_anomaly_detection.py`, `test_database.py`, `test_udimonitor_helpers.py`, `test_subscribers.py`, `test_parse_rest.py`).
- Fixed `_normalize_event` action dictionary parsing in `nucore_subscriber.py`.
- Updated `README.md` and added `.github/instructions/python.instructions.md`.

## Runtime Target
- eISY / PG3x only.
- NuCore callback integration preferred.
- SQLite-powered analytics running native C queries inside `history.db`.

## Verification
Run all unit tests:
```sh
python -m unittest discover -v
```

Run SQLite data & anomaly report:
```sh
python "import sqlite3.py"
```
