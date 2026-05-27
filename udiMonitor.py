import sys
from udi_interface import Interface, Node, LOGGER
import database
import ml_engine
from iox_subscriber import IoXEventSubscriber

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
    },
    "I_BOOLEAN": {
        "type": "range",
        "min": 0,
        "max": 1,
        "desc": "Boolean Alert Status",
        "values": {
            "0": "Normal",
            "1": "Anomaly Detected"
        }
    },
    "I_PERCENT": {
        "type": "range",
        "min": 0,
        "max": 100,
        "desc": "Percentage"
    }
}

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
    },
    "ANOMALY_TRACKER": {
        "nodedef_id": "ANOMALY_TRACKER",
        "node_type": 2,
        "drivers": [
            {"driver": "ST", "editor": "I_BOOLEAN", "uom": 2},     # Status (Alerting/Normal)
            {"driver": "GV1", "editor": "I_PERCENT", "uom": 56},   # Anomaly Score (Percent)
            {"driver": "GV2", "editor": "I_PERCENT", "uom": 56}    # Dynamic Baseline threshold 
        ],
        "commands": [
            {"id": "QUERY"}
        ]
    }
}

# =========================================================================
# NODE IMPLEMENTATIONS
# =========================================================================

class AnomalyTrackerNode(Node):
    id = 'ANOMALY_TRACKER'
    commands = {'QUERY': 'query'}
    
    # Notice we don't have hardcoded drivers array here anymore. 
    # The Polyglot engine pulls driver rules directly from the dynamic definition matching this ID.
    def __init__(self, polyglot, primary, address, name):
        super(AnomalyTrackerNode, self).__init__(polyglot, primary, address, name)

class Controller(Node):
    id = 'ML_CTRL'
    commands = {'QUERY': 'query'}

    def __init__(self, polyglot, primary, address, name):
        super(Controller, self).__init__(polyglot, primary, address, name)
        self.poly = polyglot

    def start(self):
        LOGGER.info("Initializing SQLite database...")
        database.init_db()

        LOGGER.info("Fetching credentials from PG3x...")
        iox_ip = self.poly.config.get('isyIp', '127.0.0.1')
        iox_port = self.poly.config.get('isyPort', '8080')
        iox_user = self.poly.config.get('isyUser')
        iox_pass = self.poly.config.get('isyPassword')

        LOGGER.info("Starting background Event Subscriber...")
        self.subscriber = IoXEventSubscriber(
            host=iox_ip,
            port=iox_port,
            username=iox_user,
            password=iox_pass,
            event_callback=self.process_incoming_data
        )
        self.subscriber.start()

    def process_incoming_data(self, node_id, value):
        # 1. Log to history file
        database.log_event(node_id, value)
        
        # 2. Run through ML Engine
        is_anomaly, score = ml_engine.analyze_datapoint(node_id, value)
        
        if is_anomaly:
            LOGGER.warn(f"ALERT: Anomaly detected on Node {node_id}! Score: {score}")
            addr = f"anom_{node_id.lower()[:10]}"
            
            # Check if tracker node already exists, if not instantiate it
            if not self.poly.getNode(addr):
                # Under Dynamic Profiles, the node server safely builds this node using rules mapped in NODE_DEFINITIONS
                self.poly.addNode(AnomalyTrackerNode(self.poly, self.address, addr, f"Tracker {node_id}"))
            
            tracker = self.poly.getNode(addr)
            if tracker:
                tracker.setDriver('ST', 1)      # Flashes "Anomaly Detected" in the Admin Console
                tracker.setDriver('GV1', score)  # Sends the exact calculation percentage


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