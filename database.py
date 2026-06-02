
import fnmatch
import importlib
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any

try:
    _udi_module = importlib.import_module("udi_interface")
    LOGGER = getattr(_udi_module, "LOGGER", logging.getLogger(__name__))
except Exception:  # pragma: no cover - fallback for local lint/test environments
    LOGGER = logging.getLogger(__name__)

DB_NAME = "history.db"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS node_control_static (
            node_id TEXT NOT NULL,
            control TEXT NOT NULL,
            name TEXT,
            action TEXT,
            uom INTEGER,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL,
            PRIMARY KEY (node_id, control)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events_dynamic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_ms INTEGER NOT NULL,
            ingest_time_ms INTEGER NOT NULL,
            source TEXT,
            node_id TEXT NOT NULL,
            control TEXT NOT NULL,
            value_text TEXT,
            value_num REAL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_node_control_time ON events_dynamic (node_id, control, event_time_ms)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_time ON events_dynamic (event_time_ms)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS event_filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            mode TEXT NOT NULL DEFAULT 'allow',
            source TEXT,
            node_id_pattern TEXT,
            control_pattern TEXT,
            min_value_num REAL,
            max_value_num REAL,
            allow_non_numeric INTEGER NOT NULL DEFAULT 1,
            updated_ms INTEGER NOT NULL
        )
        """
    )

    now_ms = _now_ms()
    cursor.execute(
        """
        INSERT INTO event_filters (
            enabled,
            priority,
            mode,
            source,
            node_id_pattern,
            control_pattern,
            min_value_num,
            max_value_num,
            allow_non_numeric,
            updated_ms
        )
        SELECT 1, 1000, 'allow', NULL, NULL, NULL, NULL, NULL, 1, ?
        WHERE NOT EXISTS (SELECT 1 FROM event_filters)
        """,
        (now_ms,),
    )

    # Legacy table is no longer used in this project.
    cursor.execute("DROP TABLE IF EXISTS device_logs")

    conn.commit()
    conn.close()


def upsert_static_metadata(
    node_id: str,
    control: str,
    name: str | None = None,
    action: str | None = None,
    uom: int | None = None,
    event_time_ms: int | None = None,
):
    if not node_id or not control:
        return

    seen_ms = event_time_ms if event_time_ms is not None else _now_ms()

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO node_control_static (
            node_id,
            control,
            name,
            action,
            uom,
            first_seen_ms,
            last_seen_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id, control)
        DO UPDATE SET
            name = COALESCE(excluded.name, node_control_static.name),
            action = COALESCE(excluded.action, node_control_static.action),
            uom = COALESCE(excluded.uom, node_control_static.uom),
            last_seen_ms = CASE
                WHEN excluded.last_seen_ms > node_control_static.last_seen_ms THEN excluded.last_seen_ms
                ELSE node_control_static.last_seen_ms
            END
        """,
        (node_id, control, name, action, uom, seen_ms, seen_ms),
    )
    conn.commit()
    conn.close()


def insert_dynamic_event(
    source: str | None,
    node_id: str,
    control: str,
    value: Any,
    event_time_ms: int | None,
):
    if not node_id or not control:
        return

    ingest_time_ms = _now_ms()
    event_ms = event_time_ms if event_time_ms is not None else ingest_time_ms
    value_text = None if value is None else str(value)
    value_num = _coerce_float(value)

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO events_dynamic (
            event_time_ms,
            ingest_time_ms,
            source,
            node_id,
            control,
            value_text,
            value_num
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_ms, ingest_time_ms, source, node_id, control, value_text, value_num),
    )
    conn.commit()
    conn.close()


def load_active_filters() -> list[dict[str, Any]]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            id,
            enabled,
            priority,
            mode,
            source,
            node_id_pattern,
            control_pattern,
            min_value_num,
            max_value_num,
            allow_non_numeric,
            updated_ms
        FROM event_filters
        WHERE enabled = 1
        ORDER BY priority ASC, id ASC
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def _match_pattern(value: str | None, pattern: str | None) -> bool:
    if not pattern:
        return True
    if value is None:
        return False
    return fnmatch.fnmatch(str(value), pattern)


def _value_matches_rule(value_num: float | None, rule: dict[str, Any]) -> bool:
    min_value = rule.get("min_value_num")
    max_value = rule.get("max_value_num")
    allow_non_numeric = bool(rule.get("allow_non_numeric", 1))

    if value_num is None:
        return allow_non_numeric

    if min_value is not None and value_num < float(min_value):
        return False
    if max_value is not None and value_num > float(max_value):
        return False
    return True


def event_passes_filters(
    source: str | None,
    node_id: str,
    control: str,
    value: Any,
    filters: list[dict[str, Any]] | None = None,
) -> bool:
    active_filters = filters if filters is not None else load_active_filters()
    if not active_filters:
        return True

    value_num = _coerce_float(value)

    for rule in active_filters:
        if not _match_pattern(source, rule.get("source")):
            continue
        if not _match_pattern(node_id, rule.get("node_id_pattern")):
            continue
        if not _match_pattern(control, rule.get("control_pattern")):
            continue
        if not _value_matches_rule(value_num, rule):
            continue

        mode = str(rule.get("mode", "allow")).lower()
        return mode != "block"

    return True


