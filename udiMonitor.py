import sys
import os
import json
import time
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, timezone
from udi_interface import Interface, Node, LOGGER, Custom
import database
import ml_engine
from nucore_subscriber import NuCoreEventSubscriber, NuCoreSubscriberError
from event_logger import append_event_line
from parse_rest import build_control_metadata_records

try:
    from iox_subscriber import IoXEventSubscriber
    IOX_IMPORT_ERROR = None
except Exception as err:
    # Keep startup alive when IoX fallback dependency is unavailable.
    IoXEventSubscriber = None
    IOX_IMPORT_ERROR = err

# =========================================================================
# UTILITY
# =========================================================================

def event_time_to_ms(event: dict) -> int | None:
    """Return the top-level event timestamp as Unix time in milliseconds.

    Accepts an event dict whose ``timestamp`` value is either:
    - an ISO 8601 string  (e.g. ``"2026-05-29T17:48:47.990268+00:00"``)
    - a ``datetime`` object

    Returns ``None`` when the timestamp is missing or cannot be parsed.
    """
    ts = event.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        try:
            dt = datetime.fromisoformat(str(ts))
        except ValueError:
            return None
    # Ensure the datetime is timezone-aware; treat naive as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


EVENT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_callback.jsonl")
VERSION = os.getenv("UDI_MONITOR_VERSION", "0.0.2")
DEFAULT_REST_REFRESH_ATTEMPTS = 3
DEFAULT_REST_REFRESH_BACKOFF_S = 1.0


def current_time_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _coerce_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_isy_ip(raw_value):
    """Normalize custom isy_ip input to an IPv4 host string.

    Expected input is plain IPv4 (for example 192.168.1.240). If a URL or
    host:port is provided, extract the host portion and validate it as IPv4.
    """
    if raw_value is None:
        return None

    raw_text = str(raw_value).strip()
    if not raw_text:
        return None

    candidate = raw_text
    if "://" in raw_text or "/" in raw_text or ":" in raw_text:
        parsed = urlparse(raw_text if "://" in raw_text else f"//{raw_text}")
        if parsed.hostname:
            candidate = parsed.hostname
        else:
            candidate = raw_text.split("/", 1)[0].split(":", 1)[0]
        LOGGER.warning(
            "customParams isy_ip should be plain IPv4 host; normalized input to host=%s",
            candidate,
        )

    try:
        ip_obj = ipaddress.ip_address(candidate)
    except ValueError:
        LOGGER.warning("customParams isy_ip is invalid IPv4 input: %s", raw_text)
        return None

    if ip_obj.version != 4:
        LOGGER.warning("customParams isy_ip must be IPv4: %s", raw_text)
        return None

    return str(ip_obj)


def _truncate_file(path):
    """Truncate a file in place, creating parent directories as needed."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
        LOGGER.info("Startup cleanup: truncated %s", path)
    except OSError as exc:
        LOGGER.warning("Startup cleanup: could not truncate %s: %s", path, exc)


def cleanup_startup_files(event_log_file="event_stream.jsonl"):
    """Clear runtime log/event files so each startup begins with fresh data."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    debug_log_path = os.path.join(base_dir, "logs", "debug.log")

    _truncate_file(debug_log_path)
    _truncate_file(EVENT_LOG_PATH)

    if not os.path.isabs(event_log_file):
        event_log_path = os.path.join(base_dir, event_log_file)
    else:
        event_log_path = event_log_file
    _truncate_file(event_log_path)

def log_event_to_file( node_id, control, value, name, action, event_time):
    """Append a single event-callback record as a JSON line to EVENT_LOG_PATH."""
    record = {
        "node_id": node_id,
        "control": control,
        "value": value,
        "name": name,
        "action": action,
        "event_time_ms": event_time,
    }
    try:
        with open(EVENT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        LOGGER.warning("log_event_to_file: could not write to %s: %s", EVENT_LOG_PATH, exc)

# =========================================================================
# DYNAMIC PROFILE DEFINITIONS (JSON Equivalent in Python)
# =========================================================================

# 1. Custom Editor Definitions (Replaces editors.xml)
# Instructs IoX how to display specific value scales in the UI
MY_EDITORS = {
    "I_SYSTEM_STATUS": {
        "type": "range",
        "min": 0,
        "max": 1,
        "desc": "System Status",
        "values": {
            "0": "Offline",
            "1": "Online"
        }
    }
}

# Runtime config notes (PG3x customData):
# {
#   "eventSource": "iox",  # set to "nucore" only when NuCore provider is installed/configured
#   "eventLogFile": "event_stream.jsonl",
#   "nucore": {
#     "provider_path": "iox.IoXWrapper",  # or "package.module:FactoryOrClass"
#     "provider_init": {
#       "base_url": "https://eisy-ip",
#       "username": "admin",
#       "password": "your-password",
#       "json_output": true,
#       "prompt_format_type": "shared-features"
#     },
#     "subscribe_method": "register_callback",  # optional override for non-NuCore providers
#     "start_method": "start"  # optional override
#   }
# }

# 2. Dynamic Node Definitions (Replaces nodedefs.xml)
NODE_DEFINITIONS = {
    "ML_CTRL": {
        "nodedef_id": "ML_CTRL",
        "node_type": 1,
        "drivers": [
            {"driver": "ST", "editor": "I_SYSTEM_STATUS", "uom": 2}
        ],
        "commands": [
            {"id": "QUERY"}
        ]
    }
}

# =========================================================================
# NODE IMPLEMENTATIONS
# =========================================================================

class Controller(Node):
    id = 'ML_CTRL'
    commands = {'QUERY': 'query'}

    def __init__(self, polyglot, primary, address, name):
        super(Controller, self).__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.subscriber = None
        self.event_log_file = "event_stream.jsonl"
        self.fallback_started = False
        self.control_meta_index = {}
        self.policy_stats = {"audited": 0, "skipped": 0}
        self.active_node_map = {}
        self._started = False
        self._metadata_refresh_in_progress = False
        self._last_connection_fingerprint = None
        self._rest_refresh_attempts = max(
            1,
            _coerce_int(os.getenv("UDI_REST_REFRESH_ATTEMPTS")) or DEFAULT_REST_REFRESH_ATTEMPTS,
        )
        self._rest_refresh_backoff_s = max(
            0.1,
            _coerce_float(os.getenv("UDI_REST_REFRESH_BACKOFF_S")) or DEFAULT_REST_REFRESH_BACKOFF_S,
        )
        # Modes: off, audit, enforce. Audit is default to avoid accidental data loss.
        self.timestamp_policy_mode = str(os.getenv("UDI_TIMESTAMP_POLICY_MODE", "audit")).strip().lower()
        self.custom_params = Custom(self.poly, "customparams")
        # Explicitly bind lifecycle handlers so startup always runs under PG3x.
        self.poly.subscribe(self.poly.START, self.start, self.address)
        self.poly.subscribe(self.poly.STOP, self.stop)
        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.handle_custom_params)
        LOGGER.debug(
            "Controller initialized: address=%s name=%s rest_attempts=%s rest_backoff=%.2fs",
            self.address,
            self.name,
            self._rest_refresh_attempts,
            self._rest_refresh_backoff_s,
        )

    def handle_custom_params(self, params):
        if isinstance(params, dict):
            self.custom_params = dict(params)
        else:
            self.custom_params = {}

        has_isy_ip = bool(
            self.custom_params.get("isy_ip")
            or self.custom_params.get("ISY_IP")
            or self.custom_params.get("eISY_IP")
            or self.custom_params.get("EISY_IP")
        )
        has_username = bool(
            self.custom_params.get("isy_user")
            or self.custom_params.get("ISY_USER")
            or self.custom_params.get("username")
            or self.custom_params.get("USERNAME")
        )
        has_password = bool(
            self.custom_params.get("isy_password")
            or self.custom_params.get("ISY_PASSWORD")
            or self.custom_params.get("password")
            or self.custom_params.get("PASSWORD")
        )
        LOGGER.info(
            "customParams updated: isy_ip=%s isy_user=%s isy_password=%s",
            has_isy_ip,
            has_username,
            has_password,
        )
        LOGGER.debug("customParams keys seen: %s", sorted(self.custom_params.keys()))

        if not self._started:
            return

        cfg = self._resolve_iox_connection()
        fingerprint = (
            str(cfg.get("host") or ""),
            str(cfg.get("port") or ""),
            bool(cfg.get("secure")),
            str(cfg.get("username") or ""),
            bool(cfg.get("password")),
        )
        if fingerprint == self._last_connection_fingerprint:
            return

        self._last_connection_fingerprint = fingerprint
        LOGGER.info("customParams changed connection settings; scheduling metadata refresh.")
        self._run_metadata_refresh_cycle(reason="custom_params")

    def _get_custom_params(self):
        if isinstance(self.custom_params, dict) and self.custom_params:
            return self.custom_params
        params = self.poly.config.get("customParams", {})
        if isinstance(params, dict):
            return params
        return {}

    def _is_valid_dynamic_event(self, node_id, control, value):
        if node_id is None or control is None or value is None:
            return False
        if str(node_id).strip() == "" or str(control).strip() == "":
            return False
        return True

    def stop(self):
        LOGGER.info(
            "Controller stop received. timestamp_policy_mode=%s audited=%s skipped=%s",
            self.timestamp_policy_mode,
            self.policy_stats.get("audited", 0),
            self.policy_stats.get("skipped", 0),
        )

    def start(self):
        LOGGER.info("Controller startup beginning: address=%s version=%s", self.address, VERSION)
        custom_data = self.poly.config.get("customData", {})
        if isinstance(custom_data, dict):
            self.event_log_file = custom_data.get("eventLogFile", self.event_log_file)
            LOGGER.debug("Startup customData keys: %s", sorted(custom_data.keys()))
        LOGGER.debug("Startup customParams keys: %s", sorted(self._get_custom_params().keys()))

        cleanup_startup_files(self.event_log_file)

        LOGGER.info("Initializing SQLite database...")
        database.init_db()
        LOGGER.debug("SQLite initialized; beginning metadata refresh cycle for startup")
        self._run_metadata_refresh_cycle(reason="startup")
        self.control_meta_index = database.load_control_metadata_index()
        LOGGER.info("Loaded control metadata rows: %s", len(self.control_meta_index))
        cfg = self._resolve_iox_connection()
        self._last_connection_fingerprint = (
            str(cfg.get("host") or ""),
            str(cfg.get("port") or ""),
            bool(cfg.get("secure")),
            str(cfg.get("username") or ""),
            bool(cfg.get("password")),
        )
        self._started = True

        source = self._get_event_source()
        LOGGER.info(f"Event source selected: {source}")
        LOGGER.info(f"Event log target: {self.event_log_file}")
        if source == "nucore":
            self._start_nucore_subscriber()
        else:
            self._start_iox_subscriber()

    def _get_control_meta(self, node_id, control):
        if node_id is None or control is None:
            return None
        return self.control_meta_index.get((str(node_id), str(control)))

    def _should_skip_dynamic_event(self, event, control_meta):
        if not isinstance(control_meta, dict):
            return False

        if str(control_meta.get("storage_policy") or "store_value") != "skip_value_only_change":
            return False

        mode = self.timestamp_policy_mode
        if mode == "off":
            return False

        node_id = event.get("node_id")
        control = event.get("control")
        value = event.get("value")
        reason = control_meta.get("policy_reason") or "timestamp_like"

        if mode == "audit":
            self.policy_stats["audited"] = self.policy_stats.get("audited", 0) + 1
            LOGGER.debug(
                "Timestamp-like event observed (audit): node=%s control=%s value=%s reason=%s",
                node_id,
                control,
                value,
                reason,
            )
            return False

        self.policy_stats["skipped"] = self.policy_stats.get("skipped", 0) + 1
        LOGGER.debug(
            "Timestamp-like event skipped: node=%s control=%s value=%s reason=%s",
            node_id,
            control,
            value,
            reason,
        )
        return True

    def _validate_value_with_lookup(self, value, control_meta):
        if not isinstance(control_meta, dict):
            return None

        value_num = _coerce_float(value)
        if value_num is None:
            return None

        allowed_subset = control_meta.get("allowed_subset")
        if isinstance(allowed_subset, list) and allowed_subset:
            value_int = _coerce_int(value)
            candidates = {str(value)}
            if value_int is not None:
                candidates.add(str(value_int))
            if not any(candidate in set(allowed_subset) for candidate in candidates):
                return False

        min_value = control_meta.get("min_value")
        if min_value is not None and value_num < float(min_value):
            return False

        max_value = control_meta.get("max_value")
        if max_value is not None and value_num > float(max_value):
            return False

        return True

    def _get_event_source(self):
        custom_data = self.poly.config.get("customData", {})
        if isinstance(custom_data, dict):
            return str(custom_data.get("eventSource", "iox")).lower()
        return "iox"

    def _first_non_empty(self, *values):
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _build_active_node_map(self):
        nodes = getattr(self.poly, "nodes", {})
        if not isinstance(nodes, dict):
            LOGGER.debug("Polyglot nodes container is not a dict; skipping active node map build.")
            return {}

        out = {}
        for node in nodes.values():
            address = getattr(node, "address", None)
            if not address:
                continue
            key = str(address).strip()
            if not key:
                continue
            out[key] = {
                "name": getattr(node, "name", None),
                "primary": getattr(node, "primary", None),
            }
        LOGGER.info("Active Polyglot nodes discovered: count=%s", len(out))
        LOGGER.debug("Active Polyglot node addresses: %s", sorted(out.keys()))
        return out

    def _persist_active_node_map(self, active_node_map, source):
        node_ids = sorted(active_node_map.keys()) if isinstance(active_node_map, dict) else []
        if not node_ids:
            LOGGER.info("No active Polyglot nodes available to persist.")
            return
        try:
            applied = database.bulk_upsert_node_activity(
                node_ids,
                polyglot_active=True,
                rest_seen=None,
                source=source,
                seen_ms=current_time_ms(),
            )
            LOGGER.info("Persisted active node map rows: %s", applied)
        except Exception as exc:
            LOGGER.warning("Failed to persist active node map: %s", exc)

    def _run_metadata_refresh_cycle(self, reason):
        if self._metadata_refresh_in_progress:
            LOGGER.info("Skipping metadata refresh (%s): refresh already in progress.", reason)
            return False

        self._metadata_refresh_in_progress = True
        try:
            active_map = self._build_active_node_map()
            self.active_node_map = active_map
            self._persist_active_node_map(active_map, source=reason)
            LOGGER.debug(
                "Starting metadata refresh cycle: reason=%s active_nodes=%s",
                reason,
                len(active_map),
            )
            refreshed = self._refresh_metadata_with_retry(active_map, reason=reason)
            self.control_meta_index = database.load_control_metadata_index()
            LOGGER.info(
                "Metadata refresh cycle complete: reason=%s refreshed=%s loaded_rows=%s",
                reason,
                refreshed,
                len(self.control_meta_index),
            )
            return refreshed
        finally:
            self._metadata_refresh_in_progress = False

    def _resolve_iox_connection(self):
        custom_data = self.poly.config.get("customData", {})
        custom_params = self._get_custom_params()
        iox_cfg = custom_data.get("iox", {}) if isinstance(custom_data, dict) else {}
        custom_iox_user = custom_data.get("ioxUser") if isinstance(custom_data, dict) else None
        custom_iox_pass = custom_data.get("ioxPassword") if isinstance(custom_data, dict) else None

        custom_eisy_ip = self._first_non_empty(
            custom_params.get("isy_ip"),
            custom_params.get("ISY_IP"),
            custom_params.get("eISY_IP"),
            custom_params.get("EISY_IP"),
        )
        custom_username = self._first_non_empty(
            custom_params.get("isy_user"),
            custom_params.get("ISY_USER"),
            custom_params.get("username"),
            custom_params.get("USERNAME"),
        )
        custom_password = self._first_non_empty(
            custom_params.get("isy_password"),
            custom_params.get("ISY_PASSWORD"),
            custom_params.get("password"),
            custom_params.get("PASSWORD"),
        )

        custom_iox_host = None
        if custom_eisy_ip:
            custom_iox_host = _normalize_isy_ip(custom_eisy_ip)

        host_source = "default"
        iox_host = custom_iox_host
        if iox_host:
            host_source = "customParams.isy_ip"
        elif self.poly.config.get('isyIp'):
            iox_host = self.poly.config.get('isyIp')
            host_source = "polyglot.isyIp"
        elif iox_cfg.get("host"):
            iox_host = iox_cfg.get("host")
            host_source = "customData.iox.host"
        elif iox_cfg.get("ip"):
            iox_host = iox_cfg.get("ip")
            host_source = "customData.iox.ip"
        else:
            iox_host = None

        iox_secure = iox_cfg.get("secure")
        if isinstance(iox_secure, str):
            iox_secure = iox_secure.strip().lower() in ("1", "true", "yes", "on")
        if iox_secure is None:
            iox_secure = True

        default_port = "443" if iox_secure else "80"
        iox_port = (
            self.poly.config.get('isyPort')
            or iox_cfg.get("port")
            or default_port
        )

        user_source = "default"
        iox_user = custom_username
        if iox_user:
            user_source = "customParams.isy_user"
        elif self.poly.config.get('isyUser'):
            iox_user = self.poly.config.get('isyUser')
            user_source = "polyglot.isyUser"
        elif iox_cfg.get("username"):
            iox_user = iox_cfg.get("username")
            user_source = "customData.iox.username"
        elif iox_cfg.get("user"):
            iox_user = iox_cfg.get("user")
            user_source = "customData.iox.user"
        elif custom_iox_user:
            iox_user = custom_iox_user
            user_source = "customData.ioxUser"
        else:
            iox_user = None

        pass_source = "default"
        iox_pass = custom_password
        if iox_pass:
            pass_source = "customParams.isy_password"
        elif self.poly.config.get('isyPassword'):
            iox_pass = self.poly.config.get('isyPassword')
            pass_source = "polyglot.isyPassword"
        elif iox_cfg.get("password"):
            iox_pass = iox_cfg.get("password")
            pass_source = "customData.iox.password"
        elif custom_iox_pass:
            iox_pass = custom_iox_pass
            pass_source = "customData.ioxPassword"
        else:
            iox_pass = None

        LOGGER.info(
            "Resolved IoX connection: host_source=%s user_source=%s pass_source=%s secure=%s",
            host_source,
            user_source,
            pass_source,
            bool(iox_secure),
        )
        LOGGER.debug(
            "Resolved IoX endpoint summary: host=%s port=%s secure=%s user_present=%s pass_present=%s",
            iox_host,
            iox_port,
            bool(iox_secure),
            bool(iox_user),
            bool(iox_pass),
        )

        return {
            "host": iox_host,
            "port": iox_port,
            "secure": bool(iox_secure),
            "username": iox_user,
            "password": iox_pass,
        }

    def _refresh_metadata_with_retry(self, active_node_map, reason):
        max_attempts = self._rest_refresh_attempts
        for attempt in range(1, max_attempts + 1):
            LOGGER.debug(
                "REST metadata refresh attempt starting: reason=%s attempt=%s/%s active_nodes=%s",
                reason,
                attempt,
                max_attempts,
                len(active_node_map) if isinstance(active_node_map, dict) else 0,
            )
            refreshed = self._refresh_metadata_from_rest_status(active_node_map=active_node_map)
            if refreshed:
                LOGGER.info(
                    "REST metadata refresh succeeded: reason=%s attempt=%s/%s",
                    reason,
                    attempt,
                    max_attempts,
                )
                return True

            if attempt >= max_attempts:
                break

            backoff_s = self._rest_refresh_backoff_s * (2 ** (attempt - 1))
            LOGGER.warning(
                "REST metadata refresh failed: reason=%s attempt=%s/%s retry_in=%.1fs",
                reason,
                attempt,
                max_attempts,
                backoff_s,
            )
            time.sleep(backoff_s)

        LOGGER.warning(
            "REST metadata refresh exhausted retries: reason=%s attempts=%s. Monitoring will continue.",
            reason,
            max_attempts,
        )
        return False

    def _refresh_metadata_from_rest_status(self, active_node_map=None):
        cfg = self._resolve_iox_connection()
        if not cfg.get("host") or not cfg.get("username") or not cfg.get("password"):
            LOGGER.info("Skipping REST metadata refresh: missing IoX connection settings.")
            return False

        scheme = "https" if cfg["secure"] else "http"
        rest_base_url = f"{scheme}://{cfg['host']}:{cfg['port']}/rest"
        LOGGER.info("REST metadata refresh target: %s", rest_base_url)
        LOGGER.debug(
            "REST metadata fetch starting: host=%s port=%s secure=%s username_present=%s",
            cfg.get("host"),
            cfg.get("port"),
            cfg.get("secure"),
            bool(cfg.get("username")),
        )

        try:
            records, stats = build_control_metadata_records(
                rest_base_url=rest_base_url,
                username=str(cfg["username"]),
                password=str(cfg["password"]),
            )
        except Exception as exc:
            LOGGER.warning("REST metadata refresh failed at fetch/parse stage: %s", exc)
            return False

        LOGGER.debug(
            "REST metadata fetch complete: records=%s status_nodes=%s status_properties=%s slots_loaded=%s",
            len(records),
            stats.get("status_nodes", 0),
            stats.get("status_properties", 0),
            stats.get("slots_loaded", 0),
        )

        if not records:
            LOGGER.info("REST metadata refresh returned no records: %s", stats)
            return False

        rest_node_ids = sorted({str(rec.get("node_id")) for rec in records if rec.get("node_id") is not None})
        if rest_node_ids:
            try:
                database.bulk_upsert_node_activity(
                    rest_node_ids,
                    polyglot_active=None,
                    rest_seen=True,
                    source="rest_refresh",
                    seen_ms=current_time_ms(),
                )
            except Exception as exc:
                LOGGER.warning("Failed to persist REST-seen node map: %s", exc)

        active_node_ids = set(active_node_map.keys()) if isinstance(active_node_map, dict) else set()
        if active_node_ids:
            all_count = len(records)
            records = [rec for rec in records if str(rec.get("node_id")) in active_node_ids]
            LOGGER.info(
                "REST metadata filtered by active Polyglot nodes: kept=%s dropped=%s active_nodes=%s",
                len(records),
                all_count - len(records),
                len(active_node_ids),
            )
            if not records:
                LOGGER.info("No REST metadata records match active Polyglot nodes.")
                return False

        upserted = 0
        try:
            upserted = database.bulk_upsert_static_metadata(records)
        except Exception as exc:
            LOGGER.error("Bulk REST metadata upsert failed: %s", exc)
            return False

        LOGGER.info(
            "REST metadata refresh complete: upserted=%s status_nodes=%s status_properties=%s slots_loaded=%s",
            upserted,
            stats.get("status_nodes", 0),
            stats.get("status_properties", 0),
            stats.get("slots_loaded", 0),
        )
        return upserted > 0

    def _start_nucore_subscriber(self):
        custom_data = self.poly.config.get("customData", {})
        custom_params = self._get_custom_params()
        nucore_cfg = {}
        if isinstance(custom_data, dict):
            nucore_cfg = custom_data.get("nucore", {})

        if not isinstance(nucore_cfg, dict) or not nucore_cfg.get("provider_path"):
            LOGGER.warning("NuCore config missing in customData. NuCore subscriber will not start.")
            return

        provider_init = dict(nucore_cfg.get("provider_init", {}))
        custom_eisy_ip = self._first_non_empty(
            custom_params.get("isy_ip"),
            custom_params.get("ISY_IP"),
            custom_params.get("eISY_IP"),
            custom_params.get("EISY_IP"),
        )
        custom_username = self._first_non_empty(
            custom_params.get("isy_user"),
            custom_params.get("ISY_USER"),
            custom_params.get("username"),
            custom_params.get("USERNAME"),
        )
        custom_password = self._first_non_empty(
            custom_params.get("isy_password"),
            custom_params.get("ISY_PASSWORD"),
            custom_params.get("password"),
            custom_params.get("PASSWORD"),
        )

        resolved_iox = self._resolve_iox_connection()
        custom_iox_host = _normalize_isy_ip(custom_eisy_ip) if custom_eisy_ip else None

        if custom_iox_host:
            scheme = "https" if bool(resolved_iox.get("secure")) else "http"
            port = str(resolved_iox.get("port") or ("443" if scheme == "https" else "80"))
            provider_init["base_url"] = f"{scheme}://{custom_iox_host}:{port}"
        if custom_username:
            provider_init["username"] = custom_username
        if custom_password:
            provider_init["password"] = custom_password

        if not provider_init.get("base_url") or not provider_init.get("username") or not provider_init.get("password"):
            LOGGER.warning("NuCore provider_init is incomplete. NuCore subscriber will not start.")
            return

        nucore_cfg["provider_init"] = provider_init

        LOGGER.debug(
            "NuCore startup config: provider_path=%s base_url=%s user_present=%s pass_present=%s",
            nucore_cfg.get("provider_path"),
            provider_init.get("base_url"),
            bool(provider_init.get("username")),
            bool(provider_init.get("password")),
        )
        LOGGER.info(
            "NuCore provider_init resolved: base_url=%s username=%s password=%s",
            bool(provider_init.get("base_url")),
            bool(provider_init.get("username")),
            bool(provider_init.get("password")),
        )

        LOGGER.info("Starting NuCore callback subscriber...")
        try:
            self.subscriber = NuCoreEventSubscriber(
                config=nucore_cfg,
                event_callback=self.process_incoming_event,
                error_callback=self._on_nucore_error,
            )
            self.subscriber.start()
        except NuCoreSubscriberError as err:
            LOGGER.error(f"NuCore startup failed: {err}")
            self._on_nucore_error(err)

    def _on_nucore_error(self, err):
        LOGGER.error(f"NuCore subscriber error: {err}")
        if self.fallback_started:
            return

        self.fallback_started = True
        LOGGER.warning("Falling back to IoX subscriber after NuCore startup failure.")
        self._start_iox_subscriber()

    def _start_iox_subscriber(self):
        if IoXEventSubscriber is None:
            LOGGER.error(
                "IoX subscriber unavailable: %s. Install dependencies with python3 -m pip install -r requirements.txt",
                IOX_IMPORT_ERROR,
            )
            return

        LOGGER.info("Starting IoX fallback subscriber...")
        iox_conn = self._resolve_iox_connection()
        iox_ip = iox_conn["host"]
        iox_port = iox_conn["port"]
        iox_secure = iox_conn["secure"]
        iox_user = iox_conn["username"]
        iox_pass = iox_conn["password"]

        if not iox_ip:
            LOGGER.error("IoX host is missing; set customParams isy_ip or customData.iox.host/ip.")
            return
        if not iox_user or not iox_pass:
            LOGGER.error("IoX credentials are missing; set customParams isy_user/isy_password or customData values.")
            return

        LOGGER.debug(
            "IoX startup config: host=%s port=%s secure=%s username_present=%s password_present=%s",
            iox_ip,
            iox_port,
            iox_secure,
            bool(iox_user),
            bool(iox_pass),
        )
        LOGGER.info(
            "IoX fallback config: host=%s port=%s secure=%s username=%s",
            iox_ip,
            iox_port,
            iox_secure,
            iox_user,
        )

        self.subscriber = IoXEventSubscriber(
            host=iox_ip,
            port=iox_port,
            username=iox_user,
            password=iox_pass,
            secure=iox_secure,
            event_callback=self.process_incoming_data,
        )
        self.subscriber.start()

    def process_incoming_data(self, node_or_event, value=None):
        # Backward-compatible IoX callback adapter.
        if isinstance(node_or_event, dict):
            event = dict(node_or_event)
            event.setdefault("source", "iox")
        else:
            event = {
                "source": "iox",
                "node_id": node_or_event,
                "value": value,
            }
        self.process_incoming_event(event)

    def process_incoming_event(self, event):
        event = dict(event or {})

        if "node_id" not in event:
            event["node_id"] = event.get("node") or event.get("address")

        if event.get("value") is None:
            values = event.get("values")
            if isinstance(values, dict) and values:
                # Prefer ST when present, otherwise take first available driver value.
                if "ST" in values:
                    event["value"] = values["ST"]
                    event.setdefault("control", "ST")
                else:
                    first_control = next(iter(values.keys()))
                    event.setdefault("control", first_control)
                    event["value"] = values[first_control]

        if event.get("value") is None and event.get("action") is not None:
            event["value"] = event.get("action")

        #append_event_line(event, log_file=self.event_log_file)
        #LOGGER.debug("Callback payload (full): %s", json.dumps(event, default=str, separators=(",", ":"), sort_keys=True))
        
        node_id = event.get("node_id")
        value = event.get("value")
        control = event.get("control")
        raw_action = event.get("action")
        name = event.get("fmtName")
        action = event.get("fmtAct")
        uom = event.get("uom")
        event_time = event_time_to_ms(event)
        if event_time is None:
            # Prefer source event time; fall back only when upstream omits timestamp.
            event_time = current_time_ms()

        LOGGER.debug("Event callback received: source=%s node_id=%s control=%s value=%s name=%s action=%s time=%s", event.get("source"), node_id, control,  value , name, action, event_time)
        log_event_to_file(node_id, control, value, name, action, event_time)

        if node_id is not None and control is not None:
            try:
                enum_value = None
                enum_text = None if action is None else str(action)
                if _coerce_int(uom) == 25:
                    # Prefer raw action enum when present; otherwise use normalized value.
                    enum_value = _coerce_int(raw_action)
                    if enum_value is None:
                        enum_value = _coerce_int(value)

                    if enum_text is None and enum_value is not None:
                        existing_meta = self._get_control_meta(node_id, control)
                        if isinstance(existing_meta, dict):
                            enum_map = existing_meta.get("enum_map") or {}
                            enum_text = enum_map.get(str(enum_value))

                updated_meta = database.upsert_static_metadata(
                    node_id=str(node_id),
                    control=str(control),
                    name=None if name is None else str(name),
                    action=None if action is None else str(action),
                    uom=uom,
                    source=None if event.get("source") is None else str(event.get("source")),
                    enum_value=enum_value,
                    enum_text=enum_text,
                    event_time_ms=event_time,
                )
                if isinstance(updated_meta, dict):
                    self.control_meta_index[(str(node_id), str(control))] = updated_meta
            except Exception as exc:
                LOGGER.warning("Failed static metadata upsert: node=%s control=%s err=%s", node_id, control, exc)

        if not self._is_valid_dynamic_event(node_id, control, value):
            LOGGER.debug("Ignoring event without node_id/value keys: keys=%s", sorted(event.keys()))
            return

        event["node_id"] = str(node_id)
        event["control"] = str(control)
        control_meta = self._get_control_meta(node_id, control)

        is_valid = self._validate_value_with_lookup(value, control_meta)
        if is_valid is False:
            LOGGER.warning(
                "Lookup validation out-of-range: node=%s control=%s value=%s uom=%s editor=%s",
                event["node_id"],
                event["control"],
                value,
                None if not isinstance(control_meta, dict) else control_meta.get("uom"),
                None if not isinstance(control_meta, dict) else control_meta.get("editor_id"),
            )

        if self._should_skip_dynamic_event(event, control_meta):
            return

        try:
            database.insert_dynamic_event(
                node_id=str(node_id),
                control=str(control),
                value=value,
                event_time_ms=event_time,
            )
        except Exception as exc:
            LOGGER.warning("Failed dynamic event insert: node=%s control=%s err=%s", node_id, control, exc)

        # Placeholder ML remains optional/log-only for now.
        #is_anomaly, score = ml_engine.analyze_datapoint(node_id, value)

        #if is_anomaly:
        #    LOGGER.warn(f"ALERT: Anomaly detected on Node {node_id}! Score: {score}")


# =========================================================================
# APPLICATION ENTRYPOINT
# =========================================================================

if __name__ == "__main__":
    try:
        cleanup_startup_files("event_stream.jsonl")

        # Instantiate Polyglot Core
        polyglot = Interface([])

        # Use dict-style startup options for PG3/PG3x compatibility.
        polyglot.start({"version": VERSION, "requestId": True})
        polyglot.setCustomParamsDoc()

        # Build master controller
        control = Controller(polyglot, 'ml_ctrl', 'ml_ctrl', 'ML Pattern Engine')

        # Register controller so it appears as an IoX node.
        polyglot.addNode(control, conn_status='ST', rename=True)

        polyglot.ready()
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)