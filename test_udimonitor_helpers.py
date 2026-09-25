from __future__ import annotations
import unittest
from datetime import datetime, timezone
import udiMonitor

class TestUdiMonitorHelpers(unittest.TestCase):
    def test_event_time_to_ms_iso_string(self):
        iso_str = "2026-05-29T17:48:47.990000+00:00"
        ts_ms = udiMonitor.event_time_to_ms({"timestamp": iso_str})
        self.assertIsNotNone(ts_ms)
        self.assertGreater(ts_ms, 1700000000000)

    def test_event_time_to_ms_datetime(self):
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts_ms = udiMonitor.event_time_to_ms({"timestamp": dt})
        self.assertEqual(ts_ms, int(dt.timestamp() * 1000))

    def test_event_time_to_ms_invalid_or_none(self):
        self.assertIsNone(udiMonitor.event_time_to_ms({}))
        self.assertIsNone(udiMonitor.event_time_to_ms({"timestamp": None}))
        self.assertIsNone(udiMonitor.event_time_to_ms({"timestamp": "invalid_date_string"}))

    def test_normalize_isy_ip_valid_and_urls(self):
        self.assertEqual(udiMonitor._normalize_isy_ip("192.168.1.240"), "192.168.1.240")
        self.assertEqual(udiMonitor._normalize_isy_ip("http://192.168.1.240:8080/rest"), "192.168.1.240")
        self.assertEqual(udiMonitor._normalize_isy_ip("https://192.168.1.240"), "192.168.1.240")
        self.assertEqual(udiMonitor._normalize_isy_ip("  10.0.0.5  "), "10.0.0.5")

    def test_normalize_isy_ip_invalid(self):
        self.assertIsNone(udiMonitor._normalize_isy_ip(None))
        self.assertIsNone(udiMonitor._normalize_isy_ip(""))
        self.assertIsNone(udiMonitor._normalize_isy_ip("not_an_ip"))
        # IPv6 is rejected as only IPv4 is supported
        self.assertIsNone(udiMonitor._normalize_isy_ip("::1"))

    def test_match_profile_candidate_uom_and_subset(self):
        candidate_subset = {"uom": 25, "allowed_subset": ["0", "1", "2"]}
        # In subset
        self.assertTrue(udiMonitor.Controller._match_profile_candidate(None, candidate_subset, uom=25, value=1))
        # Out of subset
        self.assertFalse(udiMonitor.Controller._match_profile_candidate(None, candidate_subset, uom=25, value=5))
        # Mismatched UOM
        self.assertFalse(udiMonitor.Controller._match_profile_candidate(None, candidate_subset, uom=17, value=1))

    def test_match_profile_candidate_range(self):
        candidate_range = {"uom": 17, "min_value": 0.0, "max_value": 100.0}
        self.assertTrue(udiMonitor.Controller._match_profile_candidate(None, candidate_range, uom=17, value=50.0))
        self.assertFalse(udiMonitor.Controller._match_profile_candidate(None, candidate_range, uom=17, value=150.0))
        self.assertFalse(udiMonitor.Controller._match_profile_candidate(None, candidate_range, uom=17, value=-10.0))

    def test_validate_value_with_lookup(self):
        meta = {"uom": 17, "min_value": -20.0, "max_value": 120.0}
        self.assertTrue(udiMonitor.Controller._validate_value_with_lookup(None, 72.0, meta))
        self.assertFalse(udiMonitor.Controller._validate_value_with_lookup(None, 150.0, meta))
        self.assertFalse(udiMonitor.Controller._validate_value_with_lookup(None, -30.0, meta))

    def test_controller_driver_definitions(self):
        self.assertEqual(udiMonitor.Controller.id, "ML_CTRL")
        self.assertIn("QUERY", udiMonitor.Controller.commands)
        driver_names = [d["driver"] for d in udiMonitor.Controller.drivers]
        for expected in ["ST", "ALARM", "GV0", "GV1", "GV2", "GV3"]:
            self.assertIn(expected, driver_names)

    def test_controller_query(self):
        from unittest.mock import MagicMock
        mock_poly = MagicMock()
        ctrl = udiMonitor.Controller(mock_poly, "ml_ctrl", "ml_ctrl", "ML Controller")
        ctrl.reportDrivers = MagicMock()
        ctrl.query()
        ctrl.reportDrivers.assert_called_once()

    def test_event_log_level_and_helper(self):
        import logging
        self.assertEqual(logging.getLevelName(udiMonitor.LOG_LEVEL_EVENT), "EVENT")
        records = []
        class TestHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = TestHandler()
        logger = logging.getLogger("test_event_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        with unittest.mock.patch("udiMonitor.LOGGER", logger):
            udiMonitor.log_alert_event("Test Anomaly Alert")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].levelname, "EVENT")
        self.assertEqual(records[0].getMessage(), "Test Anomaly Alert")

    def test_profile_files_exist_and_valid_xml(self):
        import xml.etree.ElementTree as ET
        import os

        base_dir = os.path.dirname(os.path.abspath(__file__))
        editors_path = os.path.join(base_dir, "profile", "editor", "editors.xml")
        nodedefs_path = os.path.join(base_dir, "profile", "nodedef", "nodedefs.xml")
        nls_path = os.path.join(base_dir, "profile", "nls", "en_us.txt")
        version_path = os.path.join(base_dir, "profile", "version.txt")

        self.assertTrue(os.path.isfile(editors_path), "profile/editor/editors.xml must exist")
        self.assertTrue(os.path.isfile(nodedefs_path), "profile/nodedef/nodedefs.xml must exist")
        self.assertTrue(os.path.isfile(nls_path), "profile/nls/en_us.txt must exist")
        self.assertTrue(os.path.isfile(version_path), "profile/version.txt must exist")

        # Parse XML files to ensure validity
        editors_tree = ET.parse(editors_path)
        editors_root = editors_tree.getroot()
        editor_ids = {ed.get("id") for ed in editors_root.findall("editor")}

        nodedefs_tree = ET.parse(nodedefs_path)
        nodedefs_root = nodedefs_tree.getroot()
        ml_ctrl = nodedefs_root.find(".//nodeDef[@id='ML_CTRL']")
        self.assertIsNotNone(ml_ctrl, "ML_CTRL nodeDef must be defined in nodedefs.xml")

        # Verify all sts on ML_CTRL map to defined editors
        sts = ml_ctrl.findall(".//sts/st")
        st_ids = {st.get("id"): st.get("editor") for st in sts}
        for expected in ["ST", "ALARM", "GV0", "GV1", "GV2", "GV3"]:
            self.assertIn(expected, st_ids)
            editor_ref = st_ids[expected]
            self.assertIn(editor_ref, editor_ids, f"Editor '{editor_ref}' must be defined in editors.xml")

    def test_controller_lifecycle_calls_update_profile_and_add_node(self):
        from unittest.mock import MagicMock
        poly = MagicMock()
        ctrl = udiMonitor.Controller(poly, "ml_ctrl", "ml_ctrl", "ML Pattern Engine")
        poly.updateProfile.assert_called_once()
        poly.ready.assert_called_once()
        poly.addNode.assert_called_once_with(ctrl, conn_status="ST", rename=True)

if __name__ == "__main__":
    unittest.main()
