import threading
import base64
import websocket
import xml.etree.ElementTree as ET
from udi_interface import LOGGER

class IoXEventSubscriber:
    def __init__(self, host, port, username, password, event_callback):
        self.host = host
        self.port = port
        self.callback = event_callback
        
        auth_str = f"{username}:{password}"
        self.auth_header = base64.b64encode(auth_str.encode()).decode()
        self.ws_url = f"ws://{self.host}:{self.port}/rest/subscribe"
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
        self.ws.run_forever()

    def _on_message(self, ws, message):
        try:
            # Parse the native IoX XML payload
            root = ET.fromstring(message)
            control = root.find('control')
            node = root.find('node')
            action = root.find('action')

            if control is not None and node is not None and action is not None:
                # We only care about Status changes ('ST') for ML tracking
                if control.text == 'ST':
                    self.callback(node.text, action.text)
        except Exception as e:
            # Silence parsing errors for system messages that don't match standard nodes
            pass

    def _on_error(self, ws, error):
        LOGGER.error(f"IoX WebSocket Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        LOGGER.warn("IoX WebSocket disconnected.")