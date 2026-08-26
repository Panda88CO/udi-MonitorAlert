import unittest
import parse_rest

class TestParseRest(unittest.TestCase):
    def test_parse_subset_values(self):
        self.assertIsNone(parse_rest._parse_subset_values(None))
        self.assertIsNone(parse_rest._parse_subset_values(""))
        
        # Single items
        res1 = parse_rest._parse_subset_values("0,1,2")
        self.assertEqual(res1, {"0", "1", "2"})

        # Range notation
        res2 = parse_rest._parse_subset_values("1-4,7")
        self.assertEqual(res2, {"1", "2", "3", "4", "7"})

    def test_parse_nls_text(self):
        raw = """
        # Header comment
        ND-TEMP_SENSOR-NAME = Temperature Sensor
        ST-CLITEMP-NAME = Current Temp
        ST-I_STATUS-0 = Offline
        ST-I_STATUS-1 = Online
        """
        parsed = parse_rest._parse_nls_text(raw)
        self.assertEqual(parsed.get("ND-TEMP_SENSOR-NAME"), "Temperature Sensor")
        self.assertEqual(parsed.get("ST-I_STATUS-0"), "Offline")
        self.assertEqual(parsed.get("ST-I_STATUS-1"), "Online")

    def test_slot_from_node_id(self):
        self.assertEqual(parse_rest._slot_from_node_id("n008_8b4c01000de723"), "8")
        self.assertEqual(parse_rest._slot_from_node_id("n001_sensor1"), "1")
        self.assertEqual(parse_rest._slot_from_node_id("n024_device"), "24")
        self.assertIsNone(parse_rest._slot_from_node_id("invalid_node_id"))
        self.assertIsNone(parse_rest._slot_from_node_id(None))

    def test_candidate_matches_value(self):
        candidate = {"uom": 17, "min": 0.0, "max": 100.0}
        self.assertTrue(parse_rest._candidate_matches_value(candidate, 17, "50.0"))
        self.assertFalse(parse_rest._candidate_matches_value(candidate, 17, "150.0"))
        self.assertFalse(parse_rest._candidate_matches_value(candidate, 4, "50.0"))

    def test_candidate_enum_map(self):
        candidate = {"uom": 25, "nls": "ST_ENUM", "subset": {"0", "1"}}
        slot_assets = {
            "nls": {
                "ST_ENUM-0": "Off",
                "ST_ENUM-1": "On",
            }
        }
        enum_map = parse_rest._candidate_enum_map(slot_assets, candidate)
        self.assertIsNotNone(enum_map)
        self.assertEqual(enum_map, {"0": "Off", "1": "On"})

if __name__ == "__main__":
    unittest.main()
