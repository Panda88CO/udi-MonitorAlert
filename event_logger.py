import json
import os
from datetime import datetime, timezone

from udi_interface import LOGGER

EVENT_LOG_FILE = "event_stream.jsonl"


def append_event_line(event, log_file=EVENT_LOG_FILE):
    """Append a normalized event payload to a JSONL log file."""
    payload = dict(event or {})
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    try:
        parent = os.path.dirname(log_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), default=str))
            handle.write("\n")
    except Exception as err:
        LOGGER.error(f"Failed writing event log line: {err}")
