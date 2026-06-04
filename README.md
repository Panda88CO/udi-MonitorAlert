# UDI MonitorAlert

PG3x Python node server for eISY/IoX that captures device events and logs them for later analysis.

## Current Scope

- Dynamic profile with one controller node (`ML_CTRL`)
- Event ingestion from NuCore (primary) with IoX fallback
- Append-only callback event logging in JSON Lines format
- SQLite event logging for historical storage

## Runtime Target

This project is intended to run on Universal Devices eISY with PG3x and IoX.

## Files

- `udiMonitor.py`: Node server entrypoint and controller flow
- `nucore_subscriber.py`: NuCore callback adapter
- `iox_subscriber.py`: IoX WebSocket subscriber fallback
- `database.py`: SQLite storage helper
- `ml_engine.py`: Placeholder anomaly scoring logic

## Install

1. Deploy this project as a PG3x node server package.
2. Ensure dependencies are installed via:

```sh
python3 -m pip install -r requirements.txt
```

3. Start/restart the node server from PG3x.

## Local Debugging On Windows

For local scripts and non-PG3x debugging, install only the packages that do not depend on `udi_interface`:

```sh
python -m pip install -r requirements-local.txt
```

`udi_interface` remains a runtime dependency for the actual node server entrypoint in PG3x/eISY.

## PG3x customData Example

Use this as a starting point in PG3x customData:

```json
{
  "eventSource": "nucore",
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
- Preferred PG3 custom parameters are `isy_ip`, `isy_user`, and `isy_password`.
- Backward-compatible aliases are still accepted: `eISY_IP`, `username`, `password`, and uppercase variants.
- `isy_ip` should be entered as plain IPv4 only (example: `192.168.1.240`).
- For NuCore, the system builds `customData.nucore.provider_init.base_url` from `isy_ip` plus resolved protocol/port.
- For IoX fallback, `isy_ip` maps to IoX host while protocol/port are resolved from system settings.
- On startup, REST metadata refresh uses bounded retry (`UDI_REST_REFRESH_ATTEMPTS`, `UDI_REST_REFRESH_BACKOFF_S`) and then continues monitoring even if metadata refresh is unavailable.

## PG3x customParams Example

Use these PG3x custom parameters to drive both NuCore startup and IoX fallback:

```json
{
  "isy_ip": "192.168.1.240",
  "isy_user": "admin",
  "isy_password": "YOUR_PASSWORD"
}
```

Legacy format remains supported:

```json
{
  "eISY_IP": "192.168.1.240",
  "username": "admin",
  "password": "YOUR_PASSWORD"
}
```

## Event Log Output

Event callback lines are appended to `event_callback.jsonl`.
Each line is a standalone JSON object with normalized fields such as:

- `source`
- `timestamp`
- `node_id`
- `value`

## Verification on eISY

1. Confirm PG3x install step completes without dependency errors.
2. Confirm node server starts and controller node appears.
3. Trigger a known device change.
4. Confirm a new line appears in `event_callback.jsonl`.
5. Confirm SQLite `history.db` receives the event.

## Roadmap

- Add filtering/tracking selection from customData
- Add controller telemetry/reporting drivers
- Add correlation and outlier analysis in ML layer
