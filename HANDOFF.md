# Handoff

## Project Goal
Build a PG3x Python node server for Universal Devices eISY that monitors event changes from NuCore/IoX, logs every event to a JSONL file, and later adds filtering/reporting and ML-based outlier detection.

## Current State
- Phase 1 is complete.
- Phase 2 bootstrap is in progress.
- NuCore is the preferred event source.
- IoX remains a fallback if NuCore startup fails.
- Event logging is append-only JSON Lines, one event per line.

## Completed Work
- Fixed `manifest.json` to valid JSON and updated the entrypoint to `udiMonitor.py`.
- Cleaned `requirements.txt` to only include runtime dependencies.
- Simplified `udiMonitor.py` to a single controller nodedef (`ML_CTRL`).
- Added `nucore_subscriber.py` for NuCore callback integration.
- Added `event_logger.py` for one-line-per-event JSONL logging.
- Added `README.md` and `LICENSE.md`.
- Added `STATUS.md` as a GitHub-visible progress file.

## Runtime Target
- eISY / PG3x only.
- NuCore callback integration preferred.
- JSONL event logging first, filtering/reporting later.

## NuCore Config Shape
Use PG3x customData like this:

```json
{
  "eventSource": "nucore",
  "eventLogFile": "event_stream.jsonl",
  "nucore": {
    "provider_path": "iox.IoXWrapper",
    "provider_init": {
      "base_url": "https://YOUR_EISY_IP",
      "username": "admin",
      "password": "YOUR_PASSWORD",
      "json_output": true,
      "prompt_format_type": "shared-features"
    }
  }
}
```

## Next Steps
1. Validate NuCore callback wiring on eISY with the real provider path.
2. Confirm event lines are written to the configured JSONL file.
3. Confirm SQLite event logging still works from the same event path.
4. Add filtering, reporting, and controller telemetry later.

## Notes
- Session memory is local to this Copilot environment and does not move with the repo.
- This file is the portable source of truth for continuing on another machine.
