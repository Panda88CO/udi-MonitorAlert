import os
import unittest
import tempfile
import sqlite3
import database
import ml_engine

class TestAnomalyDetection(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
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

    def test_sqlite_baseline_calculation(self):
        # Insert 20 data points around 20.0 with stddev ~ 2.0
        values = [18.0, 22.0, 19.0, 21.0, 20.0, 20.0, 18.5, 21.5, 19.5, 20.5] * 2
        for i, val in enumerate(values):
            database.insert_dynamic_event('node_1', 'CLITEMP', val, event_time_ms=1000 + i * 1000)

        baseline = database.get_sensor_baseline('node_1', 'CLITEMP')
        self.assertIsNotNone(baseline)
        self.assertEqual(baseline['count'], 20)
        self.assertAlmostEqual(baseline['mean'], 20.0, places=2)
        self.assertGreater(baseline['stddev'], 1.0)
        self.assertEqual(baseline['min'], 18.0)
        self.assertEqual(baseline['max'], 22.0)

    def test_ml_engine_z_score_anomaly(self):
        values = [20.0, 20.2, 19.8, 20.1, 19.9, 20.0, 20.3, 19.7, 20.1, 19.9] * 2
        for i, val in enumerate(values):
            database.insert_dynamic_event('node_temp', 'CLITEMP', val, event_time_ms=1000 + i * 1000)

        # Normal reading: 20.1 (should be normal)
        is_anom, score, details = ml_engine.analyze_datapoint('node_temp', 20.1, control='CLITEMP', event_time_ms=50000)
        self.assertFalse(is_anom)
        self.assertEqual(score, 0)

        # Outlier reading: 99.0 (should trigger anomaly)
        is_anom, score, details = ml_engine.analyze_datapoint('node_temp', 99.0, control='CLITEMP', event_time_ms=60000)
        self.assertTrue(is_anom)
        self.assertGreaterEqual(score, 80)
        self.assertEqual(details['type'], 'z_score')
        self.assertGreater(details['z_score'], 10.0)

    def test_ml_engine_step_spike(self):
        # Insert initial point
        database.insert_dynamic_event('node_hum', 'CLIHUM', 500.0, event_time_ms=10000)

        # Immediate drop to 50.0 within 5 seconds (10x drop glitch)
        is_anom, score, details = ml_engine.analyze_datapoint(
            'node_hum', 50.0, control='CLIHUM', event_time_ms=15000, check_spikes=True
        )
        self.assertTrue(is_anom)
        self.assertEqual(details['type'], 'step_spike')
        self.assertAlmostEqual(details['delta_value'], 450.0)

    def test_non_numeric_and_insufficient_samples(self):
        is_anom, score, details = ml_engine.analyze_datapoint('node_x', 'INVALID', control='ST')
        self.assertFalse(is_anom)
        self.assertEqual(details['reason'], 'non_numeric_value')

        database.insert_dynamic_event('node_few', 'ST', 10.0, event_time_ms=1000)
        is_anom, score, details = ml_engine.analyze_datapoint('node_few', 50.0, control='ST', event_time_ms=2000, min_samples=5)
        self.assertFalse(is_anom)
        self.assertEqual(details['reason'], 'insufficient_samples')

    def test_find_historical_outliers_query(self):
        # Insert 20 normal values and 1 outlier
        for i in range(20):
            database.insert_dynamic_event('node_hist', 'CLITEMP', 25.0 + (i % 3) * 0.5, event_time_ms=1000 + i * 1000)
        database.insert_dynamic_event('node_hist', 'CLITEMP', 150.0, event_time_ms=50000)

        outliers = database.find_historical_outliers(min_z_score=3.0, min_samples=15, limit=10)
        self.assertGreaterEqual(len(outliers), 1)
        self.assertEqual(outliers[0]['node_id'], 'node_hist')
        self.assertEqual(outliers[0]['control'], 'CLITEMP')
        self.assertEqual(outliers[0]['value'], 150.0)
        self.assertGreater(outliers[0]['z_score'], 3.0)

    def test_find_historical_spikes_query(self):
        database.insert_dynamic_event('node_spike', 'CLIHUM', 50.0, event_time_ms=10000)
        database.insert_dynamic_event('node_spike', 'CLIHUM', 500.0, event_time_ms=15000)

        spikes = database.find_historical_spikes(max_time_delta_sec=60.0, min_value_delta=10.0, limit=10)
        self.assertGreaterEqual(len(spikes), 1)
        spike = spikes[0]
        self.assertEqual(spike['node_id'], 'node_spike')
        self.assertEqual(spike['control'], 'CLIHUM')
        self.assertEqual(spike['prev_value'], 50.0)
        self.assertEqual(spike['value'], 500.0)
        self.assertEqual(spike['delta_value'], 450.0)
        self.assertEqual(spike['delta_seconds'], 5.0)
        self.assertEqual(spike['rate_per_second'], 90.0)

if __name__ == '__main__':
    unittest.main()

