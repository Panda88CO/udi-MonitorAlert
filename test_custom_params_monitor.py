from __future__ import annotations
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

    def test_discovery_rules_classification(self):
        import discovery_rules

        # 1. Irrigation
        res = discovery_rules.classify_candidate("n001_rachio", "FLOW", name="Lawn Sprinkler Zone 1", uom=36)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "irrigation")
        self.assertEqual(res[1], "spike, creep, stuck")

        # 2. Temperature
        res = discovery_rules.classify_candidate("n002_tstat", "CLITEMP", name="Living Room", uom=17)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "temperature")
        self.assertEqual(res[1], "spike, stuck, hourly")

        # 3. Power
        res = discovery_rules.classify_candidate("n003_plug", "CURRENT_POWER", name="Coffee Maker", uom=73)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "power")
        self.assertEqual(res[1], "hourly, spike")

        # 4. Tank Level
        res = discovery_rules.classify_candidate("n004_tank", "LEVEL", name="Oil Tank Level", min_value=0.0, max_value=500.0)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "tank_level")
        self.assertEqual(res[1], "spike, stuck")

        # 5. Exclusions
        # Binary switch (0-100 without power)
        res_sw = discovery_rules.classify_candidate("n005_switch", "ST", name="Kitchen Light", min_value=0.0, max_value=100.0)
        self.assertIsNone(res_sw)
        # Timestamp
        res_ts = discovery_rules.classify_candidate("n006_clock", "GV1", name="Last Run Time", is_timestamp_like=True)
        self.assertIsNone(res_ts)
        # Self-controller
        res_ctrl = discovery_rules.classify_candidate("ml_ctrl", "ST", name="Pattern Engine")
        self.assertIsNone(res_ctrl)
        # Non-telemetry device (dimmer/fan level without telemetry keywords/uom) should NOT match fallback
        res_fan = discovery_rules.classify_candidate("n007_fan", "SPEED", name="Ceiling Fan Speed", min_value=0.0, max_value=3.0, uom=25)
        self.assertIsNone(res_fan)

    def test_register_custom_category(self):
        import discovery_rules
        try:
            discovery_rules.register_category(
                category="pool_chemistry",
                description="Pool Chlorination & pH",
                uoms=(55,),
                controls=("PH", "ORP"),
                keywords=("pool", "chlorine", "ph"),
                preset="spike, stuck",
            )
            res = discovery_rules.classify_candidate("n010_pool", "PH", name="Pool Chlorine Feeder")
            self.assertIsNotNone(res)
            self.assertEqual(res[0], "pool_chemistry")
        finally:
            discovery_rules.reset_categories_to_default()

    def test_database_discover_candidate_monitors(self):
        database.upsert_static_metadata("n001_irr", "FLOW", name="Lawn Sprinklers", uom=36, uom_label="GPM")
        database.upsert_static_metadata("n002_fridge", "TEMP", name="Kitchen Fridge", uom=17, uom_label="°F")
        database.upsert_static_metadata("n003_switch", "ST", name="Hall Light", min_value=0.0, max_value=100.0)

        all_cands = database.discover_candidate_monitors()
        cand_keys = {c["canonical_key"] for c in all_cands}
        self.assertIn("n001_irr.FLOW [Lawn Sprinklers]", cand_keys)
        self.assertIn("n002_fridge.TEMP [Kitchen Fridge]", cand_keys)
        self.assertNotIn("n003_switch.ST [Hall Light]", cand_keys)

        # Test category filter
        irr_only = database.discover_candidate_monitors(category_filter="irrigation")
        self.assertEqual(len(irr_only), 1)
        self.assertEqual(irr_only[0]["category"], "irrigation")

        # Test exclude existing
        excluded = database.discover_candidate_monitors(exclude_node_controls={("n001_irr", "FLOW")})
        excl_keys = {c["canonical_key"] for c in excluded}
        self.assertNotIn("n001_irr.FLOW [Lawn Sprinklers]", excl_keys)
        self.assertIn("n002_fridge.TEMP [Kitchen Fridge]", excl_keys)

    def test_auto_populate_custom_params_workflow_and_pruning(self):
        database.upsert_static_metadata("n001_irr", "FLOW", name="Lawn Sprinklers", uom=36, uom_label="GPM")
        database.upsert_static_metadata("n002_fridge", "TEMP", name="Kitchen Fridge", uom=17, uom_label="°F")

        class MockPoly:
            START = "start"
            STOP = "stop"
            CUSTOMPARAMS = "customparams"
            def __init__(self):
                self.saved_params = None
                self.notices = {}
                self.config = {}
            def subscribe(self, *args, **kwargs):
                pass
            def setCustomParams(self, params):
                self.saved_params = dict(params)
            def addNotice(self, msg, key="default"):
                self.notices[key] = msg

        poly = MockPoly()
        ctrl = udiMonitor.Controller(poly, "primary", "ctl", "Controller")

        # User triggers auto_populate via customParams
        raw_params = {
            "isy_ip": "192.168.1.240",
            "auto_populate": "true",
        }
        ctrl._sync_custom_params_monitors(raw_params)

        # 1. Verify customParams were auto-populated
        self.assertIsNotNone(poly.saved_params)
        self.assertIn("n001_irr.FLOW [Lawn Sprinklers]", poly.saved_params)
        self.assertIn("n002_fridge.TEMP [Kitchen Fridge]", poly.saved_params)
        self.assertEqual(poly.saved_params["n001_irr.FLOW [Lawn Sprinklers]"], "spike, creep, stuck")
        self.assertEqual(poly.saved_params["n002_fridge.TEMP [Kitchen Fridge]"], "spike, stuck, hourly")
        self.assertTrue(poly.saved_params["auto_populate"].startswith("completed"))

        # 2. Verify tasks were created in database
        active = database.load_active_monitor_tasks()
        active_ids = {t["task_id"] for t in active}
        self.assertIn("n001_irr_FLOW_spike", active_ids)
        self.assertIn("n002_fridge_TEMP_spike", active_ids)

        # 3. Simulate user erasing the fridge row in PG3x and saving
        erased_params = dict(poly.saved_params)
        del erased_params["n002_fridge.TEMP [Kitchen Fridge]"]

        ctrl._sync_custom_params_monitors(erased_params)

        # Verify fridge task was pruned from database!
        active_after = database.load_active_monitor_tasks()
        active_ids_after = {t["task_id"] for t in active_after}
        self.assertIn("n001_irr_FLOW_spike", active_ids_after)
        self.assertNotIn("n002_fridge_TEMP_spike", active_ids_after)

    def test_cold_start_auto_populate(self):
        database.upsert_static_metadata("n001_irr", "FLOW", name="Lawn Sprinklers", uom=36, uom_label="GPM")

        class MockPoly:
            START = "start"
            STOP = "stop"
            CUSTOMPARAMS = "customparams"
            def __init__(self):
                self.saved_params = None
                self.notices = {}
                self.config = {"customParams": {"isy_ip": "192.168.1.240"}}
            def subscribe(self, *args, **kwargs):
                pass
            def setCustomParams(self, params):
                self.saved_params = dict(params)
            def addNotice(self, msg, key="default"):
                self.notices[key] = msg

        poly = MockPoly()
        ctrl = udiMonitor.Controller(poly, "primary", "ctl", "Controller")

        # Cold start check should detect no monitors and run auto_populate
        ctrl._check_cold_start_auto_populate()

        self.assertIsNotNone(poly.saved_params)
        self.assertIn("n001_irr.FLOW [Lawn Sprinklers]", poly.saved_params)
        self.assertTrue(poly.saved_params["auto_populate"].startswith("completed"))

    def test_custom_params_persistence_and_pg3_send(self):
        class MockPG3Poly:
            START = "start"
            STOP = "stop"
            CUSTOMPARAMS = "customparams"
            def __init__(self):
                self.sent_messages = []
                self.notices = {}
                self.config = {}
            def subscribe(self, *args, **kwargs):
                pass
            def send(self, message, msg_type="custom"):
                self.sent_messages.append((msg_type, message))
            def addNotice(self, msg, key="default"):
                self.notices[key] = msg

        poly = MockPG3Poly()
        ctrl = udiMonitor.Controller(poly, "primary", "ctl", "Controller")

        # 1. Verify handle_custom_params updates self.custom_params without destroying Custom instance
        ctrl.handle_custom_params({"isy_ip": "192.168.1.100", "test_key": "val1"})
        self.assertTrue(hasattr(ctrl.custom_params, "load"))
        self.assertEqual(ctrl._get_custom_params().get("isy_ip"), "192.168.1.100")

        # 2. Call _save_custom_params and verify poly.send was called with PG3 format
        ctrl._save_custom_params({"isy_ip": "192.168.1.100", "n001.GV1": "spike"})
        self.assertTrue(len(poly.sent_messages) > 0)
        msg_type, msg = poly.sent_messages[-1]
        self.assertEqual(msg_type, "custom")
        self.assertIn("set", msg)
        self.assertEqual(msg["set"][0]["key"], "customparams")
        self.assertEqual(msg["set"][0]["value"]["n001.GV1"], "spike")

if __name__ == "__main__":
    unittest.main()

