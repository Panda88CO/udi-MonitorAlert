import os
import unittest
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import database
import ml_engine
import parse_rest
from iox_subscriber import IoXEventSubscriber
import udiMonitor


class TestDataDrivenStateMonitoring(unittest.TestCase):
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

    def test_variable_definitions_and_values(self):
        type2_xml = (
            b"<CList>"
            b'  <e id="1" name="HERE"/>'
            b'  <e id="5" name="sHouseMode"/>'
            b"</CList>"
        )
        type1_xml = (
            b"<CList>"
            b'  <e id="2" name="iVacation"/>'
            b"</CList>"
        )
        var_val_xml = (
            b'<var type="2" id="1">'
            b"  <val>0</val>"
            b"  <ts>20260906103000</ts>"
            b"</var>"
        )

        def mock_fetch_xml(url, auth=None, timeout=10):
            if "definitions/2" in url:
                return ET.fromstring(type2_xml)
            elif "definitions/1" in url:
                return ET.fromstring(type1_xml)
            elif "vars/get/2/1" in url:
                return ET.fromstring(var_val_xml)
            return None

        with patch("parse_rest._fetch_xml_with_auth", side_effect=mock_fetch_xml):
            defs = parse_rest.fetch_variable_definitions("http://192.168.1.100/rest")
            self.assertIn("HERE", defs["by_name"])
            self.assertIn("$HERE", defs["by_name"])
            self.assertIn("$here", defs["by_name"])
            self.assertEqual(defs["by_name"]["HERE"]["id"], 1)
            self.assertEqual(defs["by_name"]["HERE"]["type"], 2)

            self.assertIn("iVacation", defs["by_name"])
            self.assertEqual(defs["by_name"]["iVacation"]["type"], 1)

            val = parse_rest.fetch_variable_value("http://192.168.1.100/rest", var_type=2, var_id=1)
            self.assertEqual(val, 0.0)

    def test_iox_subscriber_parses_variable_events(self):
        received_events = []
        sub = IoXEventSubscriber(
            host="127.0.0.1",
            port=80,
            username="admin",
            password="password",
            event_callback=lambda ev: received_events.append(ev),
        )

        var_msg = (
            '<event control="_1" action="6">'
            "<eventInfo>"
            '<var type="2" id="1">'
            "<val>0</val>"
            "<ts>20260906103000</ts>"
            "</var>"
            "</eventInfo>"
            "</event>"
        )

        sub._on_message(None, var_msg)
        self.assertEqual(len(received_events), 1)
        ev = received_events[0]
        self.assertEqual(ev.get("event_type"), "variable")
        self.assertEqual(ev.get("var_type"), 2)
        self.assertEqual(ev.get("var_id"), 1)
        self.assertEqual(ev.get("node_id"), "VAR.2.1")
        self.assertEqual(ev.get("value"), "0")

    def test_state_partitioned_database_baselines(self):
        # Insert historical data: power sensor under "home" vs "away"
        # Away readings: around 150W (140 - 160W)
        for i in range(20):
            database.insert_dynamic_event(
                node_id="n005_power",
                control="CURRENT_POWER",
                value=150.0 + (i % 5),
                event_time_ms=100000 + i * 1000,
                system_state="away",
            )

        # Home readings: around 800W (700 - 900W)
        for i in range(20):
            database.insert_dynamic_event(
                node_id="n005_power",
                control="CURRENT_POWER",
                value=800.0 + (i * 5),
                event_time_ms=200000 + i * 1000,
                system_state="home",
            )

        # Query baselines partitioned by state
        away_baseline = database.get_sensor_baseline("n005_power", "CURRENT_POWER", system_state="away")
        self.assertIsNotNone(away_baseline)
        self.assertEqual(away_baseline["count"], 20)
        self.assertAlmostEqual(away_baseline["mean"], 152.0, delta=2.0)

        home_baseline = database.get_sensor_baseline("n005_power", "CURRENT_POWER", system_state="home")
        self.assertIsNotNone(home_baseline)
        self.assertEqual(home_baseline["count"], 20)
        self.assertAlmostEqual(home_baseline["mean"], 847.5, delta=10.0)

        # Global baseline combines both
        global_baseline = database.get_sensor_baseline("n005_power", "CURRENT_POWER")
        self.assertIsNotNone(global_baseline)
        self.assertEqual(global_baseline["count"], 40)
        self.assertAlmostEqual(global_baseline["mean"], 500.0, delta=50.0)

    def test_ml_engine_state_conditioned_anomaly_evaluation(self):
        # Populate history:
        # Power in "away": mean ~150W, stddev ~2W
        for i in range(15):
            database.insert_dynamic_event(
                node_id="n005_power",
                control="CURRENT_POWER",
                value=150.0 + (i % 3),
                event_time_ms=100000 + i * 1000,
                system_state="away",
            )

        # Power in "home": mean ~600W, stddev ~100W
        for i in range(15):
            database.insert_dynamic_event(
                node_id="n005_power",
                control="CURRENT_POWER",
                value=500.0 + (i * 15),
                event_time_ms=200000 + i * 1000,
                system_state="home",
            )

        # Test value of 450W:
        # In AWAY state, 450W is an extreme anomaly (Z > 100)
        is_anom, score, details = ml_engine.analyze_datapoint(
            node_id="n005_power",
            new_value=450.0,
            control="CURRENT_POWER",
            event_time_ms=300000,
            system_state="away",
        )
        self.assertTrue(is_anom)
        self.assertGreaterEqual(score, 80)
        self.assertEqual(details.get("system_state"), "away")
        self.assertTrue(details.get("state_specific"))

        # In HOME state, 450W is within normal variance (Z < 3)
        is_anom_home, score_home, details_home = ml_engine.analyze_datapoint(
            node_id="n005_power",
            new_value=450.0,
            control="CURRENT_POWER",
            event_time_ms=300000,
            system_state="home",
        )
        self.assertFalse(is_anom_home)

    def test_quiescent_state_water_leak_detection(self):
        # In "away" state, water meter historical flow is always 0.0
        for i in range(10):
            database.insert_dynamic_event(
                node_id="n008_water",
                control="FLOW",
                value=0.0,
                event_time_ms=100000 + i * 1000,
                system_state="away",
            )

        # A new reading of 0.8 gpm while away should immediately trigger quiescent violation
        is_anom, score, details = ml_engine.analyze_datapoint(
            node_id="n008_water",
            new_value=0.8,
            control="FLOW",
            event_time_ms=200000,
            system_state="away",
        )
        self.assertTrue(is_anom)
        self.assertEqual(details.get("type"), "quiescent_state_violation")
        self.assertEqual(details.get("system_state"), "away")

    def test_periodic_testing_at_cadence(self):
        # Simulate water flow that remained continuously > 0 over a 15-minute test interval
        now_ms = 1000000
        window_ms = 15 * 60 * 1000  # 15 minutes
        start_ms = now_ms - window_ms

        # Baseline: away was 0.0
        for i in range(10):
            database.insert_dynamic_event(
                node_id="n008_water",
                control="FLOW",
                value=0.0,
                event_time_ms=start_ms - 50000 + i * 1000,
                system_state="away",
            )

        # Continuous leak events across the testing window
        for i in range(5):
            database.insert_dynamic_event(
                node_id="n008_water",
                control="FLOW",
                value=0.5 + i * 0.1,
                event_time_ms=start_ms + 10000 + i * 100000,
                system_state="away",
            )

        ml_engine.AUTONOMOUS_TEST_COOLDOWNS.clear()
        anomalies = ml_engine.evaluate_state_periodic_testing(
            system_state="away",
            test_interval_minutes=15,
            now_ms=now_ms,
        )

        self.assertGreaterEqual(len(anomalies), 1)
        leak_anom = [a for a in anomalies if a["node_id"] == "n008_water"][0]
        self.assertEqual(leak_anom["task_type"], "state_sustained_activity")
        self.assertEqual(leak_anom["severity"], "critical")
        self.assertIn("15m testing interval", leak_anom["details"]["reason"])

    def test_controller_state_configuration_and_transitions(self):
        mock_poly = MagicMock()
        mock_poly.config = {}
        ctrl = udiMonitor.Controller(mock_poly, "ml_ctrl", "ml_ctrl", "ML Controller")

        # Mock variable definitions
        ctrl.var_definitions = {
            "by_name": {
                "HERE": {"type": 2, "id": 1, "name": "HERE"},
            },
            "by_id": {(2, 1): "HERE"},
            "definitions": [{"type": 2, "id": 1, "name": "HERE"}],
        }

        # Configure customParams with state_source, state_mapping, and test cadence
        params = {
            "state_source": "$HERE",
            "state_mapping": "0:away, 1:home",
            "test_interval_minutes": "20",
        }
        ctrl.handle_custom_params(params)

        self.assertEqual(ctrl.test_interval_minutes, 20)
        self.assertEqual(ctrl.state_mapping.get("0"), "away")
        self.assertEqual(ctrl.state_mapping.get("1"), "home")
        self.assertEqual(ctrl.state_source_info.get("kind"), "variable")
        self.assertEqual(ctrl.state_source_info.get("id"), 1)

        # Incoming variable event: HERE changes to 0 (Away)
        var_event = {
            "source": "iox",
            "event_type": "variable",
            "var_type": 2,
            "var_id": 1,
            "value": 0,
            "node_id": "VAR.2.1",
            "control": "VAL",
        }
        ctrl.process_incoming_event(var_event)
        self.assertEqual(ctrl.current_system_state, "away")
        self.assertEqual(ctrl.drivers.get("GV3"), 0)

        # Incoming variable event: HERE changes to 1 (Home)
        var_event["value"] = 1
        ctrl.process_incoming_event(var_event)
        self.assertEqual(ctrl.current_system_state, "home")
        self.assertEqual(ctrl.drivers.get("GV3"), 1)


if __name__ == "__main__":
    unittest.main()

