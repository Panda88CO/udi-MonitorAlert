import os
import unittest
import tempfile
import database
import udiMonitor

class TestCustomParamsMonitor(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.orig_db_name = database.DB_NAME
        database.DB_NAME = self.temp_db.name
        database.init_db()

    def tearDown(self):
        database.DB_NAME = self.orig_db_name
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def test_parse_node_control_key(self):
        # 1. Copied string with ${sys.node...}
        node, ctrl, label = udiMonitor.parse_node_control_key("${sys.node.n012_8b4c01000cac1a.GV1}")
        self.assertEqual(node, "n012_8b4c01000cac1a")
        self.assertEqual(ctrl, "GV1")
        self.assertIsNone(label)

        # 2. sys.node prefix without ${}
        node, ctrl, label = udiMonitor.parse_node_control_key("sys.node.n012_8b4c01000cac1a.GV1")
        self.assertEqual(node, "n012_8b4c01000cac1a")
        self.assertEqual(ctrl, "GV1")

        # 3. Canonical with friendly label
        node, ctrl, label = udiMonitor.parse_node_control_key("n012_8b4c01000cac1a.GV1 [Water Temperature]")
        self.assertEqual(node, "n012_8b4c01000cac1a")
        self.assertEqual(ctrl, "GV1")
        self.assertEqual(label, "Water Temperature")

        # 4. Colon separated
        node, ctrl, label = udiMonitor.parse_node_control_key("n008_meter:FLOW")
        self.assertEqual(node, "n008_meter")
        self.assertEqual(ctrl, "FLOW")

        # 5. Node address only
        node, ctrl, label = udiMonitor.parse_node_control_key("${sys.node.n012_pool_heater}")
        self.assertEqual(node, "n012_pool_heater")
        self.assertIsNone(ctrl)

        # 6. Reserved system parameters should be ignored
        node, ctrl, label = udiMonitor.parse_node_control_key("isy_ip")
        self.assertIsNone(node)
        node, ctrl, label = udiMonitor.parse_node_control_key("unmonitored_retention_days")
        self.assertIsNone(node)

    def test_parse_monitor_options(self):
        # Comma-separated
        tasks = udiMonitor.parse_monitor_options("spike, stuck", "node_1", "CLITEMP", "Temperature")
        self.assertEqual(len(tasks), 2)
        task_types = {t["task_type"] for t in tasks}
        self.assertEqual(task_types, {"spike", "stuck_watchdog"})
        self.assertEqual(tasks[0]["name"], "Temperature Spike Alarm")

        # All keyword
        tasks_all = udiMonitor.parse_monitor_options("all", "node_2", "FLOW", "Water Flow")
        self.assertEqual(len(tasks_all), 4)

        # Overrides in parentheses
        tasks_overrides = udiMonitor.parse_monitor_options("stuck(45m), creep(0.01)", "node_3", "FLOW")
        self.assertEqual(len(tasks_overrides), 2)
        stuck_task = [t for t in tasks_overrides if t["task_type"] == "stuck_watchdog"][0]
        creep_task = [t for t in tasks_overrides if t["task_type"] == "slow_creep"][0]
        self.assertEqual(stuck_task["params"]["max_silent_minutes"], 45.0)
        self.assertEqual(creep_task["params"]["max_zero_threshold"], 0.01)

    def test_sync_custom_params_workflow_and_friendly_lookup(self):
        # 1. Seed static metadata in SQLite
        database.upsert_static_metadata("n012_pool", "GV1", name="Water Temperature", uom_label="°F")

        # Mock polyglot interface
        class MockPoly:
            START = "start"
            STOP = "stop"
            CUSTOMPARAMS = "customparams"
            def __init__(self):
                self.config = {}
                self.notices = {}
                self.saved_params = None
            def subscribe(self, *args, **kwargs):
                pass
            def setCustomParams(self, params):
                self.saved_params = dict(params)
            def addNotice(self, msg, key="default"):
                self.notices[key] = msg

        poly = MockPoly()
        ctrl = udiMonitor.Controller(poly, "primary", "ctl", "Controller")

        # 2. User inputs raw ${sys.node.n012_pool.GV1} with value "spike, stuck"
        raw_params = {
            "isy_ip": "192.168.1.240",
            "${sys.node.n012_pool.GV1}": "spike, stuck",
        }

        ctrl._sync_custom_params_monitors(raw_params)

        # Assert customParams were rewritten with friendly name
        self.assertIsNotNone(poly.saved_params)
        self.assertIn("n012_pool.GV1 [Water Temperature]", poly.saved_params)
        self.assertEqual(poly.saved_params["n012_pool.GV1 [Water Temperature]"], "spike, stuck")
        self.assertNotIn("${sys.node.n012_pool.GV1}", poly.saved_params)

        # Assert tasks were created in SQLite
        active = database.load_active_monitor_tasks()
        self.assertEqual(len(active), 2)
        task_ids = {t["task_id"] for t in active}
        self.assertIn("n012_pool_GV1_spike", task_ids)
        self.assertIn("n012_pool_GV1_stuck", task_ids)

        # Assert dashboard notice summary was updated
        self.assertIn("active_monitors_summary", poly.notices)
        self.assertIn("Water Temperature", poly.notices["active_monitors_summary"])

    def test_sync_custom_params_help_mode(self):
        database.upsert_static_metadata("n012_pool", "GV2", name="Filter Pressure", uom_label="PSI")

        class MockPoly:
            START = "start"
            STOP = "stop"
            CUSTOMPARAMS = "customparams"
            def __init__(self):
                self.saved_params = None
                self.notices = {}
            def subscribe(self, *args, **kwargs):
                pass
            def setCustomParams(self, params):
                self.saved_params = dict(params)
            def addNotice(self, msg, key="default"):
                self.notices[key] = msg

        poly = MockPoly()
        ctrl = udiMonitor.Controller(poly, "primary", "ctl", "Controller")

        # User pastes key with empty value (help/discovery mode)
        ctrl._sync_custom_params_monitors({
            "${sys.node.n012_pool.GV2}": "",
        })

        self.assertIsNotNone(poly.saved_params)
        self.assertIn("n012_pool.GV2 [Filter Pressure]", poly.saved_params)
        val = poly.saved_params["n012_pool.GV2 [Filter Pressure]"]
        self.assertIn("spike, stuck", val)
        self.assertIn("Options:", val)

if __name__ == "__main__":
    unittest.main()
