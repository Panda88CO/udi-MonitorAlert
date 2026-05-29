import threading
import base64
import ssl
import websocket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from udi_interface import LOGGER

class IoXEventSubscriber:
    def __init__(self, host, port, username, password, event_callback, secure=False):
        self.host = host
        self.port = port
        self.callback = event_callback
        self.secure = bool(secure)
        
        auth_str = f"{username}:{password}"
        self.auth_header = base64.b64encode(auth_str.encode()).decode()
        scheme = "wss" if self.secure else "ws"
        self.ws_url = f"{scheme}://{self.host}:{self.port}/rest/subscribe"
        self.ws = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        headers = [f"Authorization: Basic {self.auth_header}"]
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            header=headers,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        LOGGER.info(f"Connecting to IoX Event Stream at {self.ws_url}...")
        if self.secure:
            # eISY commonly uses self-signed certs on local LAN.
            self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        else:
            self.ws.run_forever()

    def _on_message(self, ws, message):
        try:
            # Parse the native IoX XML payload and forward a richer event object.
            root = ET.fromstring(message)
            event = {
                "source": "iox",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "attributes": dict(root.attrib or {}),
                "raw_xml": message,
            }

            for child in list(root):
                text = (child.text or "").strip()
                if text:
                    event[child.tag] = text

            node_id = event.get("node") or event.get("address")
            if node_id:
                event["node_id"] = node_id

            control = event.get("control")
            action = event.get("action")
            if control and action is not None:
                event["value"] = action
                event["values"] = {control: action}

            self.callback(event)
        except Exception as e:
            LOGGER.debug(f"Failed to parse IoX event XML: {e}")

    def _on_error(self, ws, error):
        LOGGER.error(f"IoX WebSocket Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        LOGGER.warning("IoX WebSocket disconnected.")