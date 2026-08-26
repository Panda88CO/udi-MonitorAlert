import os
import unittest
import tempfile
import database

class TestDatabase(unittest.TestCase):
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

    def test_init_db_creates_tables(self):
        conn = database._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        expected = {
            "node_control_static",
            "allowed_subset_lookup",
            "events_dynamic",
            "node_activity_map",
            "event_filters",
            "profile_control_schema",
        }
        self.assertTrue(expected.issubset(tables))

    def test_upsert_static_metadata(self):
        meta = database.upsert_static_metadata(
            node_id="node_123",
            control="ST",
            name="Living Room Light",
            action="DON",
            uom=25,
            uom_label="Index",
            source="test",
            allowed_subset=["0", "100"],
            enum_map={"0": "Off", "100": "On"},
        )
        self.assertIsNotNone(meta)
        self.assertEqual(meta["node_id"], "node_123")
        self.assertEqual(meta["control"], "ST")
        self.assertEqual(meta["name"], "Living Room Light")
        self.assertEqual(meta["uom"], 25)
        self.assertEqual(meta["allowed_subset"], ["0", "100"])
        self.assertEqual(meta["enum_map"], {"0": "Off", "100": "On"})

        meta_updated = database.upsert_static_metadata(
            node_id="node_123",
            control="ST",
            name="Living Room Main Light",
        )
        self.assertEqual(meta_updated["name"], "Living Room Main Light")
        self.assertEqual(meta_updated["uom"], 25)

    def test_bulk_upsert_static_metadata(self):
        records = [
            {"node_id": "n1", "control": "ST", "name": "Sensor 1", "uom": 17},
            {"node_id": "n2", "control": "CLITEMP", "name": "Sensor 2", "uom": 4},
        ]
        applied = database.bulk_upsert_static_metadata(records)
        self.assertEqual(applied, 2)

        index = database.load_control_metadata_index()
        self.assertIn(("n1", "ST"), index)
        self.assertIn(("n2", "CLITEMP"), index)
        self.assertEqual(index[("n1", "ST")]["name"], "Sensor 1")

    def test_insert_dynamic_event_and_coercion(self):
        database.insert_dynamic_event("node_a", "ST", "72.5", event_time_ms=1000)
        database.insert_dynamic_event("node_a", "ST", 75, event_time_ms=2000)
        database.insert_dynamic_event("node_a", "ST", "NOT_A_NUMBER", event_time_ms=3000)

        conn = database._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT event_time_ms, value FROM events_dynamic ORDER BY event_time_ms")
        rows = cursor.fetchall()
        conn.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["value"], 72.5)
        self.assertEqual(rows[1]["value"], 75.0)

    def test_event_filters(self):
        self.assertTrue(database.event_passes_filters("iox", "node_1", "ST", 50))

        conn = database._connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO event_filters (enabled, priority, mode, source, node_id_pattern, control_pattern, updated_ms) VALUES (1, 10, 'block', 'iox', 'node_blocked*', 'ST', 1000)"
        )
        conn.commit()
        conn.close()

        self.assertFalse(database.event_passes_filters("iox", "node_blocked_01", "ST", 50))
        self.assertTrue(database.event_passes_filters("iox", "node_other_01", "ST", 50))

    def test_bulk_upsert_profile_control_schema(self):
        records = [
            {
                "profile_slot": "8",
                "node_def_id": "TEMP_SENSOR",
                "control": "CLITEMP",
                "editor_id": "I_TEMP",
                "range_index": 0,
                "uom": 17,
                "uom_label": "Fahrenheit",
                "min_value": -40.0,
                "max_value": 140.0,
            }
        ]
        applied = database.bulk_upsert_profile_control_schema(records)
        self.assertEqual(applied, 1)

        schema_index = database.load_profile_control_schema_index()
        self.assertIn("CLITEMP", schema_index)
        self.assertEqual(len(schema_index["CLITEMP"]), 1)
        self.assertEqual(schema_index["CLITEMP"][0]["node_def_id"], "TEMP_SENSOR")

    def test_node_activity_map(self):
        database.upsert_node_activity("node_active_1", polyglot_active=True, source="startup")
        database.bulk_upsert_node_activity(["node_active_2", "node_active_3"], rest_seen=True, source="rest")

        conn = database._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT node_id, polyglot_active, rest_seen, last_source FROM node_activity_map ORDER BY node_id")
        rows = {r["node_id"]: dict(r) for r in cursor.fetchall()}
        conn.close()

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows["node_active_1"]["polyglot_active"], 1)
        self.assertEqual(rows["node_active_2"]["rest_seen"], 1)

if __name__ == "__main__":
    unittest.main()
