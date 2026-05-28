# UDI MonitorAlert

PG3x Python node server for eISY/IoX that captures device events and logs them for later analysis.

## Current Scope

- Dynamic profile with one controller node (`ML_CTRL`)
- Event ingestion from NuCore (primary) with IoX fallback
- Append-only event file logging in JSON Lines format
- SQLite event logging for historical storage

## Runtime Target

This project is intended to run on Universal Devices eISY with PG3x and IoX.

## Files

- `udiMonitor.py`: Node server entrypoint and controller flow
- `nucore_subscriber.py`: NuCore callback adapter
- `iox_subscriber.py`: IoX WebSocket subscriber fallback
- `event_logger.py`: One-line-per-event JSONL writer
- `database.py`: SQLite storage helper
- `ml_engine.py`: Placeholder anomaly scoring logic

## Install

1. Deploy this project as a PG3x node server package.
2. Ensure dependencies are installed via:

```sh
pip3 install -r requirements.txt
```

3. Start/restart the node server from PG3x.

## PG3x customData Example

Use this as a starting point in PG3x customData:

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

Notes:

- `provider_path` also supports `module:ClassName` format.
- If NuCore startup fails, the node server falls back to IoX subscriber mode.

## Event Log Output

Event lines are appended to `event_stream.jsonl` by default.
Each line is a standalone JSON object with normalized fields such as:

- `source`
- `timestamp`
- `node_id`
- `value`

## Verification on eISY

1. Confirm PG3x install step completes without dependency errors.
2. Confirm node server starts and controller node appears.
3. Trigger a known device change.
4. Confirm a new line appears in the JSONL event log file.
5. Confirm SQLite `history.db` receives the event.

## Roadmap

- Add filtering/tracking selection from customData
- Add controller telemetry/reporting drivers
- Add correlation and outlier analysis in ML layer
