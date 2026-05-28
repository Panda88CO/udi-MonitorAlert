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
- Keep dynamic profile dictionaries (`MY_EDITORS`, `NODE_DEFINITIONS`) consistent with node behavior.
- Preserve Node subclass IDs (`ML_CTRL`, `ANOMALY_TRACKER`) unless a migration is explicitly planned.
- Prefer targeted edits over broad refactors in controller/event-handling paths.

## External Dependencies and Runtime Assumptions
- Requires running Polyglot/IoX environment with config keys: `isyIp`, `isyPort`, `isyUser`, `isyPassword`.
- WebSocket transport currently uses `ws://` and Basic Auth header.
- `history.db` is created relative to process working directory.

## Known Risks and Pitfalls
- `manifest.json` appears malformed (extra opening/closing brace) and references `nodeserver.py` instead of the actual entrypoint `udiMonitor.py`.
- No automated tests are present.
- `ml_engine.py` currently uses placeholder thresholding (`value > 1000`).
- Subscriber parsing intentionally suppresses XML parse errors; avoid removing this behavior without a replacement strategy.
- Anomaly tracker address truncates `node_id` to 10 chars (`anom_{node_id[:10]}`), which can collide.

## Agent Editing Guidance
- Do not change integration contracts (config key names, callback signatures, driver IDs) unless requested.
- When changing event flow, validate all touched layers: `iox_subscriber.py`, `udiMonitor.py`, `database.py`, and `ml_engine.py`.
- Keep dependency additions minimal and justified in `requirements.txt`.
- If introducing tests, prefer small unit tests around `ml_engine.py` and `database.py` first.

## Suggested Next Customizations
- Add a focused instruction file for Python files (`.github/instructions/python.instructions.md`) with lint/test/typing expectations.
- Add a custom skill for safe IoX event-pipeline changes (schema checks, callback contract checks, and regression checklist).
