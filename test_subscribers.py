import unittest
from nucore_subscriber import NuCoreEventSubscriber, NuCoreSubscriberError
from iox_subscriber import IoXEventSubscriber

class TestSubscribers(unittest.TestCase):
    def test_nucore_split_provider_path(self):
        sub = NuCoreEventSubscriber.__new__(NuCoreEventSubscriber)
        mod, attr = sub._split_provider_path("iox.IoXWrapper")
        self.assertEqual(mod, "iox")
        self.assertEqual(attr, "IoXWrapper")

        mod, attr = sub._split_provider_path("package.submodule:Factory")
        self.assertEqual(mod, "package.submodule")
        self.assertEqual(attr, "Factory")

        with self.assertRaises(NuCoreSubscriberError):
            sub._split_provider_path("invalidpath")

    def test_nucore_build_init_kwargs(self):
        sub = NuCoreEventSubscriber.__new__(NuCoreEventSubscriber)
        res = sub._build_init_kwargs({"base_url": "https://192.168.1.200"})
        self.assertEqual(res["base_url"], "https://192.168.1.200")
        self.assertEqual(res["prompt_format_type"], "shared-features")
        self.assertTrue(res["json_output"])

    def test_nucore_normalize_event(self):
        sub = NuCoreEventSubscriber.__new__(NuCoreEventSubscriber)
        # Dict payload
        e1 = sub._normalize_event(({"node": "n001_sensor", "value": 42.5},), {})
        self.assertEqual(e1["node_id"], "n001_sensor")
        self.assertEqual(e1["value"], 42.5)
        self.assertEqual(e1["source"], "nucore")
        self.assertIn("timestamp", e1)

        # Keyword args
        e2 = sub._normalize_event((), {"address": "n002_switch", "action": {"value": 100}})
        self.assertEqual(e2["node_id"], "n002_switch")
        self.assertEqual(e2["value"], 100)

        # Positional args (node_id, value)
        e3 = sub._normalize_event(("n003_dimmer", 75), {})
        self.assertEqual(e3["node_id"], "n003_dimmer")
        self.assertEqual(e3["value"], 75)

    def test_iox_on_message_parsing(self):
        received_events = []
        subscriber = IoXEventSubscriber(
            host="127.0.0.1",
            port="8080",
            username="admin",
            password="pwd",
            event_callback=lambda e: received_events.append(e),
        )

        sample_xml = """<Event seqnum="123" sid="uuid:1">
            <control>ST</control>
            <action>255</action>
            <node>n008_8b4c01000de723</node>
            <fmtAct>On</fmtAct>
        </Event>"""

        subscriber._on_message(None, sample_xml)
        self.assertEqual(len(received_events), 1)
        ev = received_events[0]
        self.assertEqual(ev["source"], "iox")
        self.assertEqual(ev["node_id"], "n008_8b4c01000de723")
        self.assertEqual(ev["control"], "ST")
        self.assertEqual(ev["action"], "255")
        self.assertEqual(ev["value"], "255")
        self.assertEqual(ev["fmtAct"], "On")

    def test_iox_subscriber_missing_websocket(self):
        import iox_subscriber
        original_ws = iox_subscriber.websocket
        try:
            iox_subscriber.websocket = None
            sub = IoXEventSubscriber(
                host="127.0.0.1",
                port="8080",
                username="admin",
                password="pwd",
                event_callback=lambda e: None,
            )
            # start() should gracefully exit without throwing AttributeError
            sub.start()
            # _run() should also exit gracefully without throwing AttributeError
            sub._run()
        finally:
            iox_subscriber.websocket = original_ws


if __name__ == "__main__":
    unittest.main()
