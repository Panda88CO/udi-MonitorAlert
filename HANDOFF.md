# Handoff

## Project Goal
Build a PG3x Python node server for Universal Devices eISY that monitors event changes from NuCore/IoX, logs callback events to JSONL and SQLite, and provides in-database statistical outlier and anomaly detection.

## Current State
- Version: `0.1.8` (synchronized across `udiMonitor.py`, `manifest.json`, `server.json`, and `profile/version.txt`).
- Profile structure implemented following the exact `udi-broadlink` pattern with `profile/editor/editors.xml`, `profile/nodedef/nodedefs.xml`, `profile/nls/en_us.txt`, and `profile/version.txt`.
- Controller initialization order: `poly.updateProfile()`, `poly.ready()`, and `poly.addNode(self, conn_status='ST', rename=True)`.
- Full automated test suite (64 unit tests across 9 suites) passes cleanly in local and CI environments.

## Completed Work
- Implemented standard Polyglot profile structure in `profile/` with `ML_CTRL` node definition and matching editors for all drivers (`ST`, `ALARM`, `GV0..GV3`).
- Updated `server.json` and `manifest.json` for PG3x.
- Streamlined `udiMonitor.py` Controller startup sequence matching `udi-broadlink`.
- In-database SQLite anomaly and outlier engine operational and tested.
- Automated test suites expanded with XML schema verification and lifecycle tests.

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
