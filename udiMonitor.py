import sys
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
#   "eventSource": "nucore",  # or "iox"
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

    def start(self):
        LOGGER.info("Initializing SQLite database...")
        database.init_db()

        custom_data = self.poly.config.get("customData", {})
        if isinstance(custom_data, dict):
            self.event_log_file = custom_data.get("eventLogFile", self.event_log_file)

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
            return str(custom_data.get("eventSource", "nucore")).lower()
        return "nucore"

    def _start_nucore_subscriber(self):
        custom_data = self.poly.config.get("customData", {})
        nucore_cfg = {}
        if isinstance(custom_data, dict):
            nucore_cfg = custom_data.get("nucore", {})

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
        LOGGER.warn("Falling back to IoX subscriber after NuCore startup failure.")
        self._start_iox_subscriber()

    def _start_iox_subscriber(self):
        if IoXEventSubscriber is None:
            LOGGER.error(
                "IoX subscriber unavailable: %s. Install dependencies with python3 -m pip install -r requirements.txt",
                IOX_IMPORT_ERROR,
            )
            return

        LOGGER.info("Starting IoX fallback subscriber...")
        iox_ip = self.poly.config.get('isyIp', '127.0.0.1')
        iox_port = self.poly.config.get('isyPort', '8080')
        iox_user = self.poly.config.get('isyUser')
        iox_pass = self.poly.config.get('isyPassword')

        self.subscriber = IoXEventSubscriber(
            host=iox_ip,
            port=iox_port,
            username=iox_user,
            password=iox_pass,
            event_callback=self.process_incoming_data,
        )
        self.subscriber.start()

    def process_incoming_data(self, node_id, value):
        # Backward-compatible IoX callback adapter.
        event = {
            "source": "iox",
            "node_id": node_id,
            "value": value,
        }
        self.process_incoming_event(event)

    def process_incoming_event(self, event):
        event = dict(event or {})
        append_event_line(event, log_file=self.event_log_file)

        node_id = event.get("node_id")
        value = event.get("value")

        if node_id is None or value is None:
            return

        # Keep DB logging active during bootstrap.
        database.log_event(node_id, value)

        # Placeholder ML remains optional/log-only for now.
        is_anomaly, score = ml_engine.analyze_datapoint(node_id, value)

        if is_anomaly:
            LOGGER.warn(f"ALERT: Anomaly detected on Node {node_id}! Score: {score}")


# =========================================================================
# APPLICATION ENTRYPOINT
# =========================================================================

if __name__ == "__main__":
    try:
        # Instantiate Polyglot Core
        polyglot = Interface([])
        
        # MAGIC HAPPENS HERE: Pass your python profile configurations as arguments 
        # during startup. Polyglot converts this seamlessly to JSON and provisions IoX.
        polyglot.start(
            version='1.0.0',
            editors=MY_EDITORS,
            nodedefs=NODE_DEFINITIONS
        )
        
        # Build master controller
        control = Controller(polyglot, 'ml_ctrl', 'ml_ctrl', 'ML Pattern Engine')
        
        polyglot.ready()
        polyglot.runLoop()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)