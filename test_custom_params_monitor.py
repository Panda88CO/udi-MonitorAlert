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
        self.assertIsNone(label)

        # 3. Canonical leading bracket with friendly composite label [Node - Param]
        node, ctrl, label = udiMonitor.parse_node_control_key(
            "[SPAN 192.168.1.76 - Dryer - Energy last hour] n015_dryer.GV1"
        )
        self.assertEqual(node, "n015_dryer")
        self.assertEqual(ctrl, "GV1")
        self.assertEqual(label, "SPAN 192.168.1.76 - Dryer - Energy last hour")

        # 4. Leading bracket with wrapped ${sys.node...}
        node, ctrl, label = udiMonitor.parse_node_control_key(
            "[SPAN 192.168.1.76 - Dryer - Energy last hour] ${sys.node.n015_dryer.GV1}"
        )
        self.assertEqual(node, "n015_dryer")
        self.assertEqual(ctrl, "GV1")
        self.assertEqual(label, "SPAN 192.168.1.76 - Dryer - Energy last hour")

        # 5. Backward compatibility: legacy trailing bracket label
        node, ctrl, label = udiMonitor.parse_node_control_key("n012_8b4c01000cac1a.GV1 [Water Temperature]")
        self.assertEqual(node, "n012_8b4c01000cac1a")
        self.assertEqual(ctrl, "GV1")
        self.assertEqual(label, "Water Temperature")

        # 6. Colon separated
        node, ctrl, label = udiMonitor.parse_node_control_key("n008_meter:FLOW")
        self.assertEqual(node, "n008_meter")
        self.assertEqual(ctrl, "FLOW")
        self.assertIsNone(label)

        # 7. Node address only (optional control and optional label)
        node, ctrl, label = udiMonitor.parse_node_control_key("${sys.node.n012_pool_heater}")
        self.assertEqual(node, "n012_pool_heater")
        self.assertIsNone(ctrl)
        self.assertIsNone(label)

        # 8. Reserved system parameters should be ignored
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

        # Static / Frozen monitor parsing
        tasks_static = udiMonitor.parse_monitor_options("static(60m, 5)", "n015_dryer", "GV1", "Energy")
        self.assertEqual(len(tasks_static), 1)
        self.assertEqual(tasks_static[0]["task_type"], "static_data")
        self.assertEqual(tasks_static[0]["name"], "Energy Static Data Alarm")
        self.assertEqual(tasks_static[0]["params"]["max_stagnant_minutes"], 60.0)
        self.assertEqual(tasks_static[0]["params"]["min_updates"], 5)

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

        # Assert customParams were rewritten with friendly name in leading bracket format
        self.assertIsNotNone(poly.saved_params)
        self.assertIn("[n012: Water Temperature] n012_pool.GV1", poly.saved_params)
        self.assertEqual(poly.saved_params["[n012: Water Temperature] n012_pool.GV1"], "spike, stuck")
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

        # 3. Test composite 3-level [Node: Parent Node - Device Node - Parameter Name] formatting
        database.upsert_static_metadata(
            "n015_dryer",
            "GV1",
            name="Energy last hour",
            node_name="Dryer",
            parent_node_name="SPAN 192.168.1.76",
            uom_label="kWh",
        )
        ctrl._sync_custom_params_monitors({
            "${sys.node.n015_dryer.GV1}": "spike",
        })
        self.assertIn("[n015: SPAN 192.168.1.76 - Dryer - Energy last hour] n015_dryer.GV1", poly.saved_params)
        self.assertEqual(poly.saved_params["[n015: SPAN 192.168.1.76 - Dryer - Energy last hour] n015_dryer.GV1"], "spike")

        # 4. Optionality: User can provide raw key without bracket label and it auto-canonicalizes to 3 levels
        ctrl._sync_custom_params_monitors({
            "n015_dryer.GV1": "stuck",
        })
        self.assertIn("[n015: SPAN 192.168.1.76 - Dryer - Energy last hour] n015_dryer.GV1", poly.saved_params)

        # 5. User-supplied custom 3-level label:
        # If supplied as non-canonical (legacy trailing bracket), it rewrites to leading bracket preserving the 3 levels with node prefix
        ctrl._sync_custom_params_monitors({
            "n015_dryer.GV1 [Main Panel - Laundry - Dryer]": "spike, stuck",
        })
        self.assertIn("[n015: Main Panel - Laundry - Dryer] n015_dryer.GV1", poly.saved_params)
        self.assertEqual(poly.saved_params["[n015: Main Panel - Laundry - Dryer] n015_dryer.GV1"], "spike, stuck")
        tasks = database.load_active_monitor_tasks()
        dryer_tasks = [t for t in tasks if t.get("node_id_pattern") == "n015_dryer"]
        self.assertTrue(len(dryer_tasks) > 0)
        self.assertTrue(all("Main Panel - Laundry - Dryer" in t.get("name", "") for t in dryer_tasks))

        # 6. Test 2-level [Device Node - Parameter Name] when no parent exists
        database.upsert_static_metadata(
            "n012_heater", "GV1", name="Target Temp", node_name="Pool Heater", uom_label="°F"
        )
        ctrl._sync_custom_params_monitors({
            "n012_heater.GV1": "spike",
        })
        self.assertIn("[n012: Pool Heater - Target Temp] n012_heater.GV1", poly.saved_params)

        # 7. Test 1-level [Device Node] when control has no parameter name (or raw code ST)
        database.upsert_static_metadata(
            "n012_pump", "ST", node_name="Pool Pump"
        )
        ctrl._sync_custom_params_monitors({
            "n012_pump.ST": "stuck",
        })
        self.assertIn("[n012: Pool Pump] n012_pump.ST", poly.saved_params)

        # 8. User specific case: existing custom key without node number prefix upgrades seamlessly
        ctrl._sync_custom_params_monitors({
            "[SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST": "spike, stuck",
        })
        self.assertIn("[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST", poly.saved_params)
        self.assertNotIn("[SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST", poly.saved_params)

        # 9. Deduplication check: key already having node prefix is not duplicated
        ctrl._sync_custom_params_monitors({
            "[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST": "spike, stuck",
        })
        self.assertIn("[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST", poly.saved_params)
        self.assertNotIn("[n012: n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST", poly.saved_params)

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
        self.assertIn("[n012: Filter Pressure] n012_pool.GV2", poly.saved_params)
        val = poly.saved_params["[n012: Filter Pressure] n012_pool.GV2"]
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
        database.upsert_static_metadata(
            "n001_irr",
            "FLOW",
            name="Lawn Sprinklers",
            node_name="Yard Irrigation",
            parent_node_name="Rachio Hub",
            uom=36,
            uom_label="GPM",
        )
        database.upsert_static_metadata("n002_fridge", "TEMP", name="Kitchen Fridge", uom=17, uom_label="°F")
        database.upsert_static_metadata("n003_switch", "ST", name="Hall Light", min_value=0.0, max_value=100.0)

        all_cands = database.discover_candidate_monitors()
        cand_keys = {c["canonical_key"] for c in all_cands}
        self.assertIn("[n001: Rachio Hub - Yard Irrigation - Lawn Sprinklers] n001_irr.FLOW", cand_keys)
        self.assertIn("[n002: Kitchen Fridge] n002_fridge.TEMP", cand_keys)
        self.assertNotIn("n003_switch.ST", str(cand_keys))

        # Test category filter
        irr_only = database.discover_candidate_monitors(category_filter="irrigation")
        self.assertEqual(len(irr_only), 1)
        self.assertEqual(irr_only[0]["category"], "irrigation")

        # Test exclude existing
        excluded = database.discover_candidate_monitors(exclude_node_controls={("n001_irr", "FLOW")})
        excl_keys = {c["canonical_key"] for c in excluded}
        self.assertNotIn("[n001: Rachio Hub - Yard Irrigation - Lawn Sprinklers] n001_irr.FLOW", excl_keys)
        self.assertIn("[n002: Kitchen Fridge] n002_fridge.TEMP", excl_keys)

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
        self.assertIn("[n001: Lawn Sprinklers] n001_irr.FLOW", poly.saved_params)
        self.assertIn("[n002: Kitchen Fridge] n002_fridge.TEMP", poly.saved_params)
        self.assertEqual(poly.saved_params["[n001: Lawn Sprinklers] n001_irr.FLOW"], "spike, creep, stuck")
        self.assertEqual(poly.saved_params["[n002: Kitchen Fridge] n002_fridge.TEMP"], "spike, stuck, hourly")
        self.assertTrue(poly.saved_params["auto_populate"].startswith("completed"))

        # 2. Verify tasks were created in database
        active = database.load_active_monitor_tasks()
        active_ids = {t["task_id"] for t in active}
        self.assertIn("n001_irr_FLOW_spike", active_ids)
        self.assertIn("n002_fridge_TEMP_spike", active_ids)

        # 3. Simulate user erasing the fridge row in PG3x and saving
        erased_params = dict(poly.saved_params)
        del erased_params["[n002: Kitchen Fridge] n002_fridge.TEMP"]

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
        self.assertIn("[n001: Lawn Sprinklers] n001_irr.FLOW", poly.saved_params)
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

    def test_extract_node_number_and_canonical_formatting(self):
        # 1. extract_node_number tests
        self.assertEqual(database.extract_node_number("n012_8b4c01000cac1a"), "n012")
        self.assertEqual(database.extract_node_number("n015_dryer"), "n015")
        self.assertEqual(database.extract_node_number("n001"), "n001")
        self.assertEqual(database.extract_node_number("ZW004_1"), "ZW004")
        self.assertEqual(database.extract_node_number("zb002_sensor"), "ZB002")
        self.assertEqual(database.extract_node_number("node_1"), "node_1")
        self.assertIsNone(database.extract_node_number("unknown_device"))
        self.assertIsNone(database.extract_node_number(None))
        self.assertIsNone(database.extract_node_number(""))

        # 2. build_friendly_label with node_id
        lbl = database.build_friendly_label(
            node_name="SY Waterfall Right Flug",
            param_name="Watt",
            node_id="n012_8b4c01000cac1a",
        )
        self.assertEqual(lbl, "n012: SY Waterfall Right Flug - Watt")

        # 3. format_canonical_param_key upgrades user's exact example
        k1 = database.format_canonical_param_key(
            "n012_8b4c01000cac1a", "ST", "SY Waterfall Right Flug - Watt"
        )
        self.assertEqual(k1, "[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST")

        # 4. Leading bracket without prefix
        k2 = database.format_canonical_param_key(
            "n012_8b4c01000cac1a", "ST", "[SY Waterfall Right Flug - Watt]"
        )
        self.assertEqual(k2, "[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST")

        # 5. Already prefixed - no duplication
        k3 = database.format_canonical_param_key(
            "n012_8b4c01000cac1a", "ST", "[n012: SY Waterfall Right Flug - Watt]"
        )
        self.assertEqual(k3, "[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST")

        # 6. Leading hyphen/space prefix stripped before inserting colon
        k4 = database.format_canonical_param_key(
            "n012_8b4c01000cac1a", "ST", "n012 - SY Waterfall Right Flug - Watt"
        )
        self.assertEqual(k4, "[n012: SY Waterfall Right Flug - Watt] n012_8b4c01000cac1a.ST")

        # 7. Bare key without label
        k5 = database.format_canonical_param_key("n012_8b4c01000cac1a", "ST", None)
        self.assertEqual(k5, "n012_8b4c01000cac1a.ST")


if __name__ == "__main__":
    unittest.main()

