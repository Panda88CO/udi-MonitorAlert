import sys
import os
import json
from datetime import datetime, timezone
from udi_interface import Interface, Node, LOGGER
import database
import ml_engine
from nucore_subscriber import NuCoreEventSubscriber, NuCoreSubscriberError
from event_logger import append_event_line

try:
    from iox_subscriber import IoXEventSubscriber
    IOX_IMPORT_ERROR = None
except Exception as err:
    # Keep startup alive when IoX fallback dependency is unavailable.
    IoXEventSubscriber = None
    IOX_IMPORT_ERROR = err

# =========================================================================
# UTILITY
# =========================================================================

def event_time_to_ms(event: dict) -> int | None:
    """Return the top-level event timestamp as Unix time in milliseconds.

    Accepts an event dict whose ``timestamp`` value is either:
    - an ISO 8601 string  (e.g. ``"2026-05-29T17:48:47.990268+00:00"``)
    - a ``datetime`` object

    Returns ``None`` when the timestamp is missing or cannot be parsed.
    """
    ts = event.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        try:
            dt = datetime.fromisoformat(str(ts))
        except ValueError:
            return None
    # Ensure the datetime is timezone-aware; treat naive as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


EVENT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_callback.jsonl")


def _truncate_file(path):
    """Truncate a file in place, creating parent directories as needed."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
        LOGGER.info("Startup cleanup: truncated %s", path)
    except OSError as exc:
        LOGGER.warning("Startup cleanup: could not truncate %s: %s", path, exc)


def cleanup_startup_files(event_log_file="event_stream.jsonl"):
    """Clear runtime log/event files so each startup begins with fresh data."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    debug_log_path = os.path.join(base_dir, "logs", "debug.log")

    _truncate_file(debug_log_path)
    _truncate_file(EVENT_LOG_PATH)

    if not os.path.isabs(event_log_file):
        event_log_path = os.path.join(base_dir, event_log_file)
    else:
        event_log_path = event_log_file
    _truncate_file(event_log_path)

def log_event_to_file(source, node_id, control, value, name, action, event_time):
    """Append a single event-callback record as a JSON line to EVENT_LOG_PATH."""
    record = {
        "source": source,
        "node_id": node_id,
        "control": control,
        "value": value,
        "name": name,
        "action": action,
        "event_time_ms": event_time,
    }
    try:
        with open(EVENT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        LOGGER.warning("log_event_to_file: could not write to %s: %s", EVENT_LOG_PATH, exc)

# =========================================================================
# DYNAMIC PROFILE DEFINITIONS (JSON Equivalent in Python)
# =========================================================================

# 1. Custom Editor Definitions (Replaces editors.xml)
# Instructs IoX how to display specific value scales in the UI
MY_EDITORS = {
    "I_SYSTEM_STATUS": {
        "type": "range",
        "min": 0,
        "max": 1,
        "desc": "System Status",
        "values": {
            "0": "Offline",
            "1": "Online"
        }
    }
}

# Runtime config notes (PG3x customData):
# {
#   "eventSource": "iox",  # set to "nucore" only when NuCore provider is installed/configured
#   "eventLogFile": "event_stream.jsonl",
#   "nucore": {
#     "provider_path": "iox.IoXWrapper",  # or "package.module:FactoryOrClass"
#     "provider_init": {
#       "base_url": "https://eisy-ip",
#       "username": "admin",
#       "password": "your-password",
#       "json_output": true,
#       "prompt_format_type": "shared-features"
#     },
#     "subscribe_method": "register_callback",  # optional override for non-NuCore providers
#     "start_method": "start"  # optional override
#   }
# }

# 2. Dynamic Node Definitions (Replaces nodedefs.xml)
NODE_DEFINITIONS = {
    "ML_CTRL": {
        "nodedef_id": "ML_CTRL",
        "node_type": 1,
        "drivers": [
            {"driver": "ST", "editor": "I_SYSTEM_STATUS", "uom": 2}
        ],
        "commands": [
            {"id": "QUERY"}
        ]
    }
}

DEFAULT_NUCORE_CONFIG = {
    "provider_path": "iox.IoXWrapper",
    "provider_init": {
        "base_url": "https://192.168.1.204",
        "username": os.getenv("NUCORE_USERNAME", "christian.olgaard@gmail.com"),
        "password": os.getenv("NUCORE_PASSWORD", "coe123COE"),
        "json_output": True,
        "prompt_format_type": "shared-features",
    },
}

DEFAULT_IOX_CONFIG = {
    "host": "192.168.1.204",
    "port": "443",
    "secure": True,
    "username": os.getenv("IOX_USERNAME", "christian.olgaard@gmail.com"),
    "password": os.getenv("IOX_PASSWORD", "coe123COE"),
}

# =========================================================================
# NODE IMPLEMENTATIONS
# =========================================================================

class Controller(Node):
    id = 'ML_CTRL'
    commands = {'QUERY': 'query'}

    def __init__(self, polyglot, primary, address, name):
        super(Controller, self).__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.subscriber = None
        self.event_log_file = "event_stream.jsonl"
        self.fallback_started = False

        # Explicitly bind lifecycle handlers so startup always runs under PG3x.
        self.poly.subscribe(self.poly.START, self.start, self.address)
        self.poly.subscribe(self.poly.STOP, self.stop)

    def stop(self):
        LOGGER.info("Controller stop received.")

    def start(self):
        custom_data = self.poly.config.get("customData", {})
        if isinstance(custom_data, dict):
            self.event_log_file = custom_data.get("eventLogFile", self.event_log_file)

        cleanup_startup_files(self.event_log_file)

        LOGGER.info("Initializing SQLite database...")
        database.init_db()

        source = self._get_event_source()
        LOGGER.info(f"Event source selected: {source}")
        LOGGER.info(f"Event log target: {self.event_log_file}")
        if source == "nucore":
            self._start_nucore_subscriber()
        else:
            self._start_iox_subscriber()

    def _get_event_source(self):
        custom_data = self.poly.config.get("customData", {})
        if isinstance(custom_data, dict):
            return str(custom_data.get("eventSource", "iox")).lower()
        return "iox"

    def _start_nucore_subscriber(self):
        custom_data = self.poly.config.get("customData", {})
        nucore_cfg = {}
        if isinstance(custom_data, dict):
            nucore_cfg = custom_data.get("nucore", {})

        if not isinstance(nucore_cfg, dict) or not nucore_cfg.get("provider_path"):
            LOGGER.warning("NuCore config missing in customData. Using built-in defaults.")
            nucore_cfg = {
                "provider_path": DEFAULT_NUCORE_CONFIG["provider_path"],
                "provider_init": dict(DEFAULT_NUCORE_CONFIG["provider_init"]),
            }

        LOGGER.info("Starting NuCore callback subscriber...")
        try:
            self.subscriber = NuCoreEventSubscriber(
                config=nucore_cfg,
                event_callback=self.process_incoming_event,
                error_callback=self._on_nucore_error,
            )
            self.subscriber.start()
        except NuCoreSubscriberError as err:
            LOGGER.error(f"NuCore startup failed: {err}")
            self._on_nucore_error(err)

    def _on_nucore_error(self, err):
        LOGGER.error(f"NuCore subscriber error: {err}")
        if self.fallback_started:
            return

        self.fallback_started = True
        LOGGER.warning("Falling back to IoX subscriber after NuCore startup failure.")
        self._start_iox_subscriber()

    def _start_iox_subscriber(self):
        if IoXEventSubscriber is None:
            LOGGER.error(
                "IoX subscriber unavailable: %s. Install dependencies with python3 -m pip install -r requirements.txt",
                IOX_IMPORT_ERROR,
            )
            return

        LOGGER.info("Starting IoX fallback subscriber...")
        custom_data = self.poly.config.get("customData", {})
        iox_cfg = custom_data.get("iox", {}) if isinstance(custom_data, dict) else {}
        custom_iox_user = custom_data.get("ioxUser") if isinstance(custom_data, dict) else None
        custom_iox_pass = custom_data.get("ioxPassword") if isinstance(custom_data, dict) else None

        iox_ip = (
            self.poly.config.get('isyIp')
            or iox_cfg.get("host")
            or iox_cfg.get("ip")
            or DEFAULT_IOX_CONFIG["host"]
        )
        iox_port = (
            self.poly.config.get('isyPort')
            or iox_cfg.get("port")
            or DEFAULT_IOX_CONFIG["port"]
        )
        iox_secure = iox_cfg.get("secure", DEFAULT_IOX_CONFIG["secure"])
        if isinstance(iox_secure, str):
            iox_secure = iox_secure.strip().lower() in ("1", "true", "yes", "on")
        iox_user = (
            self.poly.config.get('isyUser')
            or iox_cfg.get("username")
            or iox_cfg.get("user")
            or custom_iox_user
            or DEFAULT_IOX_CONFIG["username"]
        )
        iox_pass = (
            self.poly.config.get('isyPassword')
            or iox_cfg.get("password")
            or custom_iox_pass
            or DEFAULT_IOX_CONFIG["password"]
        )

        if not iox_user or not iox_pass:
            LOGGER.error("IoX credentials are missing; set isyUser/isyPassword or IOX_USERNAME/IOX_PASSWORD.")
            return

        LOGGER.info(
            "IoX fallback config: host=%s port=%s secure=%s username=%s",
            iox_ip,
            iox_port,
            iox_secure,
            iox_user,
        )

        self.subscriber = IoXEventSubscriber(
            host=iox_ip,
            port=iox_port,
            username=iox_user,
            password=iox_pass,
            secure=iox_secure,
            event_callback=self.process_incoming_data,
        )
        self.subscriber.start()

    def process_incoming_data(self, node_or_event, value=None):
        # Backward-compatible IoX callback adapter.
        if isinstance(node_or_event, dict):
            event = dict(node_or_event)
            event.setdefault("source", "iox")
        else:
            event = {
                "source": "iox",
                "node_id": node_or_event,
                "value": value,
            }
        self.process_incoming_event(event)

    def process_incoming_event(self, event):
        event = dict(event or {})

        if "node_id" not in event:
            event["node_id"] = event.get("node") or event.get("address")

        if event.get("value") is None:
            values = event.get("values")
            if isinstance(values, dict) and values:
                # Prefer ST when present, otherwise take first available driver value.
                if "ST" in values:
                    event["value"] = values["ST"]
                    event.setdefault("control", "ST")
                else:
                    first_control = next(iter(values.keys()))
                    event.setdefault("control", first_control)
                    event["value"] = values[first_control]

        if event.get("value") is None and event.get("action") is not None:
            event["value"] = event.get("action")

        append_event_line(event, log_file=self.event_log_file)
        #LOGGER.debug("Callback payload (full): %s", json.dumps(event, default=str, separators=(",", ":"), sort_keys=True))
        
        node_id = event.get("node_id")
        value = event.get("value")
        control = event.get("control")
        name = event.get("ftmName")
        action = event.get("ftmAction")
        event_time = event_time_to_ms(event)

        LOGGER.debug("Event callback received: source=%s node_id=%s control=%s value=%s name=%s action=%s time=%s", event.get("source"), node_id, control,  value , name, action, event_time)
        log_event_to_file(event.get("source"), node_id, control, value, name, action, event_time)

        if node_id is None or value is None:
            LOGGER.debug("Ignoring event without node_id/value keys: keys=%s", sorted(event.keys()))
            return

        # Keep DB logging active during bootstrap.
        #database.log_event(node_id, value)

        # Placeholder ML remains optional/log-only for now.
        #is_anomaly, score = ml_engine.analyze_datapoint(node_id, value)

        #if is_anomaly:
        #    LOGGER.warn(f"ALERT: Anomaly detected on Node {node_id}! Score: {score}")


# =========================================================================
# APPLICATION ENTRYPOINT
# =========================================================================

if __name__ == "__main__":
    try:
        cleanup_startup_files("event_stream.jsonl")

        # Instantiate Polyglot Core
        polyglot = Interface([])

        # Use dict-style startup options for PG3/PG3x compatibility.
        polyglot.start({"version": "1.0.0", "requestId": True})

        # Build master controller
        control = Controller(polyglot, 'ml_ctrl', 'ml_ctrl', 'ML Pattern Engine')

        # Register controller so it appears as an IoX node.
        polyglot.addNode(control, conn_status='ST', rename=True)

        polyglot.ready()
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)