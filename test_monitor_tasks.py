import os
import unittest
import tempfile
import database
import ml_engine

class TestMonitorTasks(unittest.TestCase):
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

    def test_task_crud_and_bulk_upsert(self):
        task = database.upsert_monitor_task(
            task_id="leak_1",
            name="Main Leak Monitor",
            task_type="slow_creep",
            node_id_pattern="n008_water",
            control_pattern="FLOW",
            params={"max_zero_threshold": 0.05, "window_minutes": 180},
            severity="critical",
            enabled=True,
            cooldown_ms=7200000,
        )
        self.assertIsNotNone(task)
        self.assertEqual(task["task_id"], "leak_1")
        self.assertEqual(task["task_type"], "slow_creep")
        self.assertEqual(task["severity"], "critical")
        self.assertEqual(task["params"]["max_zero_threshold"], 0.05)

        records = [
            {"task_id": "watch_1", "task_type": "stuck_watchdog", "node_id_pattern": "n008_*", "control_pattern": "*"},
            {"task_id": "spike_1", "task_type": "spike", "node_id_pattern": "n008_temp", "control_pattern": "CLITEMP"},
        ]
        applied = database.bulk_upsert_monitor_tasks(records)
        self.assertEqual(applied, 2)

        active = database.load_active_monitor_tasks()
        self.assertEqual(len(active), 3)

    def test_spike_task_detector(self):
        # Configure spike monitor task
        task = {
            "task_id": "temp_spike",
            "name": "Temp Spike Alarm",
            "task_type": "spike",
            "node_id_pattern": "node_temp",
            "control_pattern": "CLITEMP",
            "params": {"z_threshold": 3.0, "max_value": 85.0},
            "severity": "critical",
            "cooldown_ms": 3600000,
        }

        # Seed 20 baseline points around 20.0 C
        for i in range(20):
            database.insert_dynamic_event("node_temp", "CLITEMP", 20.0 + (i % 2) * 0.5, event_time_ms=1000 + i * 1000)

        # Normal reading (20.2 C)
        triggered_normal = ml_engine.evaluate_live_event_tasks("node_temp", "CLITEMP", 20.2, event_time_ms=50000, tasks=[task])
        self.assertEqual(len(triggered_normal), 0)

        # Outlier reading (99.0 C) exceeding both Z-score and ceiling
        triggered_spike = ml_engine.evaluate_live_event_tasks("node_temp", "CLITEMP", 99.0, event_time_ms=60000, tasks=[task])
        self.assertEqual(len(triggered_spike), 1)
        self.assertEqual(triggered_spike[0]["task_id"], "temp_spike")
        self.assertEqual(triggered_spike[0]["severity"], "critical")

    def test_contextual_hourly_detector(self):
        task = {
            "task_id": "hourly_power",
            "name": "Night Power Anomaly",
            "task_type": "contextual_hourly",
            "node_id_pattern": "node_pwr",
            "control_pattern": "WATTS",
            "params": {"z_threshold": 3.0, "min_samples": 5},
            "severity": "warning",
            "cooldown_ms": 3600000,
        }

        # Seed historical points: hour 3 UTC normally draws ~10W
        # 1780000000 s is arbitrary base timestamp
        base_s = 1780000000 - (1780000000 % 86400) + (3 * 3600)  # exactly hour 3 UTC
        for day in range(10):
            t_ms = (base_s - day * 86400) * 1000
            database.insert_dynamic_event("node_pwr", "WATTS", 10.0 + (day % 2), event_time_ms=t_ms)

        # Test new reading at hour 3 with 500W draw
        now_hour_3_ms = base_s * 1000
        anomalies = ml_engine.evaluate_live_event_tasks("node_pwr", "WATTS", 500.0, event_time_ms=now_hour_3_ms, tasks=[task])
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["task_type"], "contextual_hourly")
        self.assertEqual(anomalies[0]["details"]["hour_of_day"], 3)

    def test_stuck_watchdog_detector(self):
        task = {
            "task_id": "freezer_watchdog",
            "name": "Freezer Sensor Watchdog",
            "task_type": "stuck_watchdog",
            "node_id_pattern": "freezer_*",
            "control_pattern": "CLITEMP",
            "params": {"max_silent_minutes": 60},
            "severity": "critical",
            "cooldown_ms": 3600000,
        }

        now_ms = 100000000
        # Insert event 3 hours ago (silent for 180 min)
        database.insert_dynamic_event("freezer_1", "CLITEMP", -18.0, event_time_ms=now_ms - (180 * 60000))
        # Insert event 10 minutes ago (healthy)
        database.insert_dynamic_event("freezer_2", "CLITEMP", -19.0, event_time_ms=now_ms - (10 * 60000))

        triggered = ml_engine.evaluate_periodic_tasks(tasks=[task], now_ms=now_ms)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["node_id"], "freezer_1")
        self.assertEqual(triggered[0]["task_id"], "freezer_watchdog")
        self.assertGreater(triggered[0]["details"]["silent_minutes"], 120)

    def test_slow_creep_detector(self):
        task = {
            "task_id": "night_water_leak",
            "name": "Night Water Trickle Leak",
            "task_type": "slow_creep",
            "node_id_pattern": "water_meter",
            "control_pattern": "FLOW",
            "params": {"window_minutes": 180, "max_zero_threshold": 0.05, "min_samples": 5},
            "severity": "critical",
            "cooldown_ms": 3600000,
        }

        now_ms = 200000000
        # Insert continuous flow of 0.2 GPM (never hitting zero) across the past 3 hours
        for i in range(10):
            database.insert_dynamic_event("water_meter", "FLOW", 0.2 + (i % 2) * 0.05, event_time_ms=now_ms - (10 - i) * 15 * 60000)

        triggered = ml_engine.evaluate_periodic_tasks(tasks=[task], now_ms=now_ms)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["task_type"], "slow_creep")
        self.assertEqual(triggered[0]["details"]["threshold"], 0.05)
        self.assertGreater(triggered[0]["details"]["min_value_observed"], 0.05)

    def test_task_cooldown_throttling(self):
        task = {
            "task_id": "throttled_alarm",
            "task_type": "spike",
            "node_id_pattern": "sensor_x",
            "control_pattern": "ST",
            "params": {"max_value": 50.0},
            "cooldown_ms": 60000, # 60s cooldown
            "last_triggered_ms": 100000,
        }

        # 10s after last trigger (in cooldown -> suppressed)
        trig_suppressed = ml_engine.evaluate_live_event_tasks("sensor_x", "ST", 100.0, event_time_ms=110000, tasks=[task])
        self.assertEqual(len(trig_suppressed), 0)

        # 70s after last trigger (cooldown expired -> allowed)
        trig_allowed = ml_engine.evaluate_live_event_tasks("sensor_x", "ST", 100.0, event_time_ms=170000, tasks=[task])
        self.assertEqual(len(trig_allowed), 1)

    def test_prune_events_dual_retention(self):
        # Setup active monitor task for water meter FLOW
        database.upsert_monitor_task(
            task_id="water_mon",
            task_type="spike",
            node_id_pattern="n008_water",
            control_pattern="FLOW",
        )

        now_ms = 1000 * 86400 * 1000  # arbitrary current time (day 1000)
        day_ms = 86400 * 1000

        # 1. Unmonitored event 45 days old (older than 30d -> should be PRUNED)
        database.insert_dynamic_event("unmon_node", "TEMP", 21.0, event_time_ms=now_ms - (45 * day_ms))

        # 2. Unmonitored event 15 days old (younger than 30d -> should be KEPT)
        database.insert_dynamic_event("unmon_node", "TEMP", 22.0, event_time_ms=now_ms - (15 * day_ms))

        # 3. Monitored event 60 days old (older than 30d, but younger than 365d -> should be KEPT)
        database.insert_dynamic_event("n008_water", "FLOW", 1.5, event_time_ms=now_ms - (60 * day_ms))

        # 4. Monitored event 400 days old (older than 365d -> should be PRUNED)
        database.insert_dynamic_event("n008_water", "FLOW", 2.0, event_time_ms=now_ms - (400 * day_ms))

        # Run dual retention prune
        result = database.prune_events_dual_retention(unmonitored_days=30, monitored_days=365, now_ms=now_ms)

        self.assertEqual(result["unmonitored_deleted"], 1)
        self.assertEqual(result["monitored_deleted"], 1)
        self.assertEqual(result["total_deleted"], 2)

        # Verify database contents
        conn = database._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT node_id, control, event_time_ms FROM events_dynamic ORDER BY event_time_ms")
        remaining = cursor.fetchall()
        conn.close()

        self.assertEqual(len(remaining), 2)
        remaining_records = [(r[0], r[1]) for r in remaining]
        self.assertIn(("unmon_node", "TEMP"), remaining_records)
        self.assertIn(("n008_water", "FLOW"), remaining_records)

if __name__ == "__main__":
    unittest.main()
