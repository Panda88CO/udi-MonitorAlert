import importlib
import threading
from typing import Any, Dict
from datetime import datetime, timezone

from types import ModuleType

from udi_interface import LOGGER


class NuCoreSubscriberError(Exception):
    pass


class NuCoreEventSubscriber:
    """Adapter that installs a callback on a NuCore provider object."""

    def __init__(self, config, event_callback, error_callback=None):
        self.config = config or {}
        self.callback = event_callback
        self.error_callback = error_callback
        self.provider = None
        self.thread = None

        self._validate_config()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _validate_config(self):
        provider_path = self.config.get("provider_path")
        if not provider_path or not isinstance(provider_path, str):
            raise NuCoreSubscriberError(
                "Missing/invalid NuCore provider_path. Expected module.path:FactoryOrClass or module.path.FactoryOrClass"
            )

        # Validate format by trying to split using supported syntaxes.
        try:
            self._split_provider_path(provider_path)
        except NuCoreSubscriberError as err:
            raise NuCoreSubscriberError(
                f"Missing/invalid NuCore provider_path. {err}"
            )

    def _run(self):
        try:
            self.provider = self._load_provider()
            self._install_callback(self.provider)
            self._start_provider(self.provider)
            LOGGER.info("NuCore callback subscriber started.")
        except Exception as err:
            wrapped = NuCoreSubscriberError(f"NuCore subscriber startup failed: {err}")
            LOGGER.error(str(wrapped))
            if callable(self.error_callback):
                try:
                    self.error_callback(wrapped)
                except Exception as callback_err:
                    LOGGER.error(f"NuCore error callback failed: {callback_err}")

    def _load_provider(self):
        provider_path = self.config.get("provider_path")
        if not isinstance(provider_path, str):
            raise NuCoreSubscriberError(
                "NuCore provider_path must be a string in format module.path:FactoryOrClass"
            )

        module_name, attr_name = self._split_provider_path(provider_path)
        module = importlib.import_module(module_name)
        provider_attr = getattr(module, attr_name)

        init_kwargs = self.config.get("provider_init", {})
        if callable(provider_attr):
            return provider_attr(**self._build_init_kwargs(init_kwargs))

        return provider_attr

    def _split_provider_path(self, provider_path: str):
        # Accept both "package.module:Class" and "package.module.Class".
        if ":" in provider_path:
            return provider_path.split(":", 1)

        parts = provider_path.rsplit(".", 1)
        if len(parts) != 2:
            raise NuCoreSubscriberError(
                f"Invalid provider_path '{provider_path}'. Expected module.path:Class or module.path.Class"
            )
        return parts[0], parts[1]

    def _build_init_kwargs(self, init_kwargs):
        if not isinstance(init_kwargs, dict):
            return {}

        kwargs = dict(init_kwargs)
        # NuCore IoXWrapper signature includes prompt_format_type; provide sane default when omitted.
        kwargs.setdefault("prompt_format_type", "shared-features")
        kwargs.setdefault("json_output", True)
        return kwargs

    def _install_callback(self, provider):
        # Prefer NuCore contract first.
        subscribe_events = getattr(provider, "subscribe_events", None)
        if callable(subscribe_events):
            self._install_nucore_callback(subscribe_events)
            LOGGER.info("NuCore callback registered using provider.subscribe_events().")
            return

        method_name = self.config.get("subscribe_method")
        candidate_methods = []

        if method_name:
            candidate_methods.append(method_name)

        candidate_methods.extend([
            "register_callback",
            "register_event_callback",
            "add_callback",
            "subscribe",
            "on_event",
        ])

        for name in candidate_methods:
            method = getattr(provider, name, None)
            if method is None:
                continue

            if self._try_callback_signatures(method):
                LOGGER.info(f"NuCore callback registered using provider.{name}().")
                return

        raise NuCoreSubscriberError(
            "Could not register callback on NuCore provider. Set customData.nucore.subscribe_method explicitly."
        )

    def _install_nucore_callback(self, subscribe_events_method):
        async def on_message(message):
            self._on_event(message)

        async def on_connect():
            LOGGER.info("NuCore event stream connected.")

        async def on_disconnect():
            LOGGER.warning("NuCore event stream disconnected.")

        subscribe_events_method(
            on_message_callback=on_message,
            on_connect_callback=on_connect,
            on_disconnect_callback=on_disconnect,
        )

    def _try_callback_signatures(self, method):
        attempts = [
            lambda: method(self._on_event),
            lambda: method(callback=self._on_event),
            lambda: method(event_callback=self._on_event),
            lambda: method("event", self._on_event),
        ]

        for attempt in attempts:
            try:
                attempt()
                return True
            except TypeError:
                continue
            except Exception as err:
                LOGGER.warning(f"NuCore callback registration attempt failed: {err}")
                continue

        return False

    def _start_provider(self, provider):
        method_name = self.config.get("start_method")
        if method_name:
            start_method = getattr(provider, method_name, None)
            if callable(start_method):
                start_method()
            return

        default_start = getattr(provider, "start", None)
        if callable(default_start):
            default_start()

    def _on_event(self, *args, **kwargs):
        event = self._normalize_event(args, kwargs)
        try:
            self.callback(event)
        except Exception as err:
            LOGGER.error(f"Failed processing NuCore event callback: {err}")

    def _normalize_event(self, args, kwargs):
        raw: Dict[str, Any]

        if len(args) == 1 and isinstance(args[0], dict):
            raw = dict(args[0])
        elif kwargs:
            raw = dict(kwargs)
        elif len(args) >= 2:
            raw = {"node_id": args[0], "value": args[1]}
        else:
            raw = {"payload": [str(x) for x in args]}

        event: Dict[str, Any] = dict(raw)
        event.setdefault("source", "nucore")
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

        if "node_id" not in event:
            for key in ["node", "address", "id", "name"]:
                if key in event:
                    event["node_id"] = event[key]
                    break

        if "value" not in event:
            action = event.get("action")
            if isinstance(action, dict) and "value" in action:
                event["value"] = action.get("value")
            for key in ["new_value", "action", "val"]:
                if key in event:
                    event["value"] = event[key]
                    break

        return event
