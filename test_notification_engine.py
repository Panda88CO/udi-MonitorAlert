import os
import unittest
from unittest.mock import patch, MagicMock
import notification_engine
import udiMonitor

class TestNotificationEngine(unittest.TestCase):
    def test_format_alert_message_spike(self):
        alert = {
            "task_id": "pool_temp_spike",
            "task_name": "Pool Temp Spike",
            "task_type": "spike",
            "node_id": "n012_pool",
            "control": "CLITEMP",
            "value": 105.0,
            "score": 95,
            "severity": "critical",
            "timestamp_ms": 1700000000000,
            "details": {
                "z_score": 4.5,
                "mean": 82.0,
                "stddev": 5.1,
                "rate_per_second": 3.2,
            },
        }

        subj, text_body, html_body = notification_engine.format_alert_message(alert, device_name="Pool Heater")
        self.assertIn("[ALERT - CRITICAL]", subj)
        self.assertIn("Pool Heater (CLITEMP)", subj)
        self.assertIn("105.0", subj)

        self.assertIn("Device:      n012_pool [Pool Heater]", text_body)
        self.assertIn("Z-Score:       4.5", text_body)
        self.assertIn("Baseline Mean: 82.0", text_body)

        self.assertIn("<h2>⚠️ Anomaly Detected</h2>", html_body)
        self.assertIn("Pool Heater", html_body)
        self.assertIn("105.0", html_body)

    def test_format_alert_message_stuck(self):
        alert = {
            "task_id": "freezer_watchdog",
            "task_type": "stuck_watchdog",
            "node_id": "freezer_sensor",
            "control": "CLITEMP",
            "value": -15.0,
            "score": 90,
            "severity": "warning",
            "details": {"silent_minutes": 185.0},
        }
        subj, text_body, html_body = notification_engine.format_alert_message(alert)
        self.assertIn("Silent / Stuck Sensor", subj)
        self.assertIn("Time Silent:   185.0 minutes", text_body)

    @patch("smtplib.SMTP")
    def test_send_email_notification_tls(self, mock_smtp):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password": "secret_password",
            "notify_email_to": "alert@example.com, other@example.com",
        }

        success = notification_engine.send_email_notification(
            config=config,
            subject="Test Alert",
            text_body="Test alert body",
        )
        self.assertTrue(success)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@example.com", "secret_password")
        mock_server.sendmail.assert_called_once()

    @patch("smtplib.SMTP_SSL")
    def test_send_email_notification_ssl(self, mock_smtp_ssl):
        mock_server = MagicMock()
        mock_smtp_ssl.return_value.__enter__.return_value = mock_server

        config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "user@example.com",
            "smtp_password": "secret_password",
            "notify_email_to": "alert@example.com",
        }

        success = notification_engine.send_email_notification(
            config=config,
            subject="Test Alert",
            text_body="Test alert body",
        )
        self.assertTrue(success)
        mock_server.login.assert_called_once_with("user@example.com", "secret_password")
        mock_server.sendmail.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_send_udmobile_iox_notification(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        config = {
            "host": "192.168.1.240",
            "port": "8080",
            "username": "admin",
            "password": "pwd",
            "secure": False,
            "notify_udmobile_content_id": "2",
            "notify_udmobile_recipient_id": "3",
        }

        success = notification_engine.send_udmobile_iox_notification(
            config=config,
            alert={"node_id": "test_node"},
            subject="Alert",
            body="Body",
        )
        self.assertTrue(success)
        mock_urlopen.assert_called_once()
        req_arg = mock_urlopen.call_args[0][0]
        self.assertIn("192.168.1.240:8080/rest/networking/notify/2/3", req_arg.full_url)

    def test_controller_queue_alert_and_drivers(self):
        class MockPoly:
            START = "start"
            STOP = "stop"
            CUSTOMPARAMS = "customparams"
            def __init__(self):
                self.config = {}
            def subscribe(self, *args, **kwargs):
                pass
            def setCustomParams(self, params):
                pass

        poly = MockPoly()
        ctrl = udiMonitor.Controller(poly, "primary", "ctl", "Controller")

        alert = {
            "task_id": "water_burst",
            "task_type": "spike",
            "node_id": "water_meter",
            "control": "FLOW",
            "value": 18.5,
            "score": 99,
        }

        ctrl._queue_alert(alert)

        # Check driver values on Controller
        self.assertEqual(ctrl.drivers.get("ALARM"), 1)
        self.assertEqual(ctrl.drivers.get("GV0"), 99)
        self.assertEqual(ctrl.drivers.get("GV1"), 1)  # spike code = 1
        self.assertEqual(ctrl.drivers.get("GV2"), 18.5)

        # Check queue
        item = ctrl._notification_queue.get_nowait()
        self.assertIsNotNone(item)
        self.assertEqual(item[0]["task_id"], "water_burst")

if __name__ == "__main__":
    unittest.main()
