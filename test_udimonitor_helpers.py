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

if __name__ == "__main__":
    unittest.main()
