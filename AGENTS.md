# AGENTS.md

## Purpose
This repository is a Python Polyglot node server for IoX/ISY event monitoring and anomaly alerting.
Agents should prioritize safe, minimal changes and preserve runtime behavior for live home-automation integrations.

## Quick Commands
- Install deps: `pip3 install -r requirements.txt`
- Optional install script: `./install.sh`
- Run node server: `python3 udiMonitor.py`

## Key Files
- `udiMonitor.py`: Entry point, Polyglot initialization, dynamic nodedef/editor config, anomaly response workflow.
- `iox_subscriber.py`: WebSocket subscription client for IoX event stream and XML event parsing.
- `database.py`: SQLite persistence (`history.db`) and event logging.
- `ml_engine.py`: Anomaly scoring logic (currently placeholder threshold logic).
- `manifest.json`: Node server metadata consumed by Polyglot tooling.

## Architecture Flow
1. `IoXEventSubscriber` receives WebSocket events (`/rest/subscribe`).
2. `Controller.process_incoming_data` logs each datapoint via `database.log_event`.
3. `ml_engine.analyze_datapoint` computes anomaly status/score.
4. On anomaly, controller creates/updates `AnomalyTrackerNode` and sets drivers.

## Project Conventions
- Use `udi_interface.LOGGER` for logging.
- Standard Polyglot XML profile files in `profile/` (`profile/editor/editors.xml`, `profile/nodedef/nodedefs.xml`, `profile/nls/en_us.txt`, `profile/version.txt`) are used following the exact pattern in `udi-broadlink`.
- In `Controller.__init__`, invoke `self.poly.updateProfile()`, `self.poly.ready()`, and `self.poly.addNode(self, conn_status='ST', rename=True)`.
- Keep in-code profile definitions (`MY_EDITORS`, `NODE_DEFINITIONS`) consistent with XML definitions.
- Preserve Node subclass IDs (`ML_CTRL`) unless a migration is explicitly planned.
- Prefer targeted edits over broad refactors in controller/event-handling paths.

## External Dependencies and Runtime Assumptions
- Requires running Polyglot/IoX environment with config keys: `isy_ip` (or `isyIp`), `isy_port`, `isy_user`, `isy_password`.
- WebSocket transport currently uses `ws://` and Basic Auth header.
- `history.db` is created relative to process working directory.

## Current State & Architecture
- `manifest.json` and `server.json` are valid and specify `udiMonitor.py` entrypoint (v0.1.7).
- Standard XML profile directory `profile/` installed with `ML_CTRL` node definition and matching editors.
- Full automated test suite (64 tests across 9 suites) passes cleanly in local and CI environments.

## Agent Editing Guidance
- Do not change integration contracts (config key names, callback signatures, driver IDs) unless requested.
- When changing event flow, validate all touched layers: `iox_subscriber.py`, `udiMonitor.py`, `database.py`, and `ml_engine.py`.
- Keep dependency additions minimal and justified in `requirements.txt`.
- Run `python3 -m unittest discover -v` to verify changes.

## Suggested Next Customizations
- Add a focused instruction file for Python files (`.github/instructions/python.instructions.md`) with lint/test/typing expectations.
- Add a custom skill for safe IoX event-pipeline changes (schema checks, callback contract checks, and regression checklist).
