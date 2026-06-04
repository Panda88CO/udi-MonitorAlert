
import fnmatch
import importlib
import sqlite3
import logging
import json
import hashlib
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


def _coerce_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def _get_table_columns(cursor: sqlite3.Cursor, table_name: str) -> list[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def _ensure_node_control_static_schema(cursor: sqlite3.Cursor):
    columns = set(_get_table_columns(cursor, "node_control_static"))
    if "enum_map_json" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN enum_map_json TEXT")
    if "uom_label" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN uom_label TEXT")
    if "source" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN source TEXT")
    if "is_timestamp_like" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN is_timestamp_like INTEGER NOT NULL DEFAULT 0")
    if "storage_policy" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN storage_policy TEXT NOT NULL DEFAULT 'store_value'")
    if "policy_reason" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN policy_reason TEXT")
    if "refreshed_at_ms" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN refreshed_at_ms INTEGER")
    if "editor_id" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN editor_id TEXT")
    if "nls_prefix" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN nls_prefix TEXT")
    if "min_value" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN min_value REAL")
    if "max_value" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN max_value REAL")
    if "allowed_subset_json" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN allowed_subset_json TEXT")
    if "allowed_subset_id" not in columns:
        cursor.execute("ALTER TABLE node_control_static ADD COLUMN allowed_subset_id INTEGER")


def _ensure_allowed_subset_lookup_schema(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS allowed_subset_lookup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subset_hash TEXT NOT NULL UNIQUE,
            subset_json TEXT NOT NULL UNIQUE
        )
        """
    )


def _normalize_allowed_subset(allowed_subset: list[str] | None) -> list[str] | None:
    if not allowed_subset:
        return None
    normalized = sorted({str(v) for v in allowed_subset if str(v) != ""})
    return normalized if normalized else None


def _get_or_create_allowed_subset_id(
    cursor: sqlite3.Cursor,
    allowed_subset: list[str] | None,
) -> tuple[int | None, str | None]:
    normalized = _normalize_allowed_subset(allowed_subset)
    if not normalized:
        return None, None

    subset_json = json.dumps(normalized, separators=(",", ":"))
    subset_hash = hashlib.sha1(subset_json.encode("utf-8")).hexdigest()

    cursor.execute(
        """
        INSERT INTO allowed_subset_lookup (subset_hash, subset_json)
        VALUES (?, ?)
        ON CONFLICT(subset_hash)
        DO UPDATE SET subset_json = excluded.subset_json
        """,
        (subset_hash, subset_json),
    )
    cursor.execute(
        "SELECT id FROM allowed_subset_lookup WHERE subset_hash = ?",
        (subset_hash,),
    )
    row = cursor.fetchone()
    return (int(row["id"]) if row else None), subset_json


def _backfill_allowed_subset_lookup(cursor: sqlite3.Cursor):
    columns = set(_get_table_columns(cursor, "node_control_static"))
    if "allowed_subset_json" not in columns or "allowed_subset_id" not in columns:
        return

    cursor.execute(
        """
        SELECT node_id, control, allowed_subset_json
        FROM node_control_static
        WHERE allowed_subset_id IS NULL AND allowed_subset_json IS NOT NULL
        """
    )
    rows = cursor.fetchall()
    for row in rows:
        subset = _decode_subset(row["allowed_subset_json"])
        subset_id, _ = _get_or_create_allowed_subset_id(cursor, subset)
        if subset_id is None:
            continue
        cursor.execute(
            """
            UPDATE node_control_static
            SET allowed_subset_id = ?
            WHERE node_id = ? AND control = ?
            """,
            (subset_id, row["node_id"], row["control"]),
        )


def _derive_storage_policy(uom: int | None) -> tuple[int, str, str]:
    # UOM 151 is commonly used for timestamp-like values and can create low-value churn.
    if _coerce_int(uom) == 151:
        return 1, "skip_value_only_change", "uom151_timestamp_like"
    return 0, "store_value", "default_store"


def _decode_enum_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if isinstance(decoded, dict):
        return {str(k): str(v) for k, v in decoded.items()}
    return {}


def _decode_subset(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(decoded, list):
        return [str(v) for v in decoded]
    return None


def _to_control_meta(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "node_id": row["node_id"],
        "control": row["control"],
        "name": row["name"],
        "action": row["action"],
        "uom": row["uom"],
        "uom_label": row["uom_label"],
        "source": row["source"],
        "editor_id": row["editor_id"],
        "nls_prefix": row["nls_prefix"],
        "min_value": row["min_value"],
        "max_value": row["max_value"],
        "allowed_subset": _decode_subset(row["allowed_subset_json"]),
        "enum_map": _decode_enum_map(row["enum_map_json"]),
        "is_timestamp_like": bool(row["is_timestamp_like"]),
        "storage_policy": row["storage_policy"] or "store_value",
        "policy_reason": row["policy_reason"],
        "first_seen_ms": row["first_seen_ms"],
        "last_seen_ms": row["last_seen_ms"],
        "refreshed_at_ms": row["refreshed_at_ms"],
    }


def _ensure_events_dynamic_numeric_schema(cursor: sqlite3.Cursor):
    columns = _get_table_columns(cursor, "events_dynamic")
    expected = {"id", "event_time_ms", "node_id", "control", "value"}

    if set(columns) == expected:
        return

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events_dynamic_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_ms INTEGER NOT NULL,
            node_id TEXT NOT NULL,
            control TEXT NOT NULL,
            value REAL NOT NULL
        )
        """
    )

    # Copy from whichever legacy value columns exist.
    if "value" in columns:
        value_expr = "value"
    elif "value_num" in columns:
        value_expr = "value_num"
    elif "value_text" in columns:
        value_expr = "CAST(value_text AS REAL)"
    else:
        value_expr = "NULL"

    cursor.execute(
        f"""
        INSERT INTO events_dynamic_new (id, event_time_ms, node_id, control, value)
        SELECT id, event_time_ms, node_id, control, {value_expr}
        FROM events_dynamic
        WHERE {value_expr} IS NOT NULL
        """
    )

    cursor.execute("DROP TABLE events_dynamic")
    cursor.execute("ALTER TABLE events_dynamic_new RENAME TO events_dynamic")


def _ensure_node_activity_map_schema(cursor: sqlite3.Cursor):
    columns = set(_get_table_columns(cursor, "node_activity_map"))
    if "node_id" not in columns:
        return
    if "polyglot_active" not in columns:
        cursor.execute("ALTER TABLE node_activity_map ADD COLUMN polyglot_active INTEGER NOT NULL DEFAULT 0")
    if "rest_seen" not in columns:
        cursor.execute("ALTER TABLE node_activity_map ADD COLUMN rest_seen INTEGER NOT NULL DEFAULT 0")
    if "last_source" not in columns:
        cursor.execute("ALTER TABLE node_activity_map ADD COLUMN last_source TEXT")
    if "first_seen_ms" not in columns:
        cursor.execute("ALTER TABLE node_activity_map ADD COLUMN first_seen_ms INTEGER NOT NULL DEFAULT 0")
    if "last_seen_ms" not in columns:
        cursor.execute("ALTER TABLE node_activity_map ADD COLUMN last_seen_ms INTEGER NOT NULL DEFAULT 0")


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
            enum_map_json TEXT,
            uom_label TEXT,
            source TEXT,
            is_timestamp_like INTEGER NOT NULL DEFAULT 0,
            storage_policy TEXT NOT NULL DEFAULT 'store_value',
            policy_reason TEXT,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL,
            refreshed_at_ms INTEGER,
            editor_id TEXT,
            nls_prefix TEXT,
            min_value REAL,
            max_value REAL,
            allowed_subset_json TEXT,
            PRIMARY KEY (node_id, control)
        )
        """
    )

    _ensure_node_control_static_schema(cursor)
    _ensure_allowed_subset_lookup_schema(cursor)
    _backfill_allowed_subset_lookup(cursor)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events_dynamic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_ms INTEGER NOT NULL,
            node_id TEXT NOT NULL,
            control TEXT NOT NULL,
            value REAL NOT NULL
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
        CREATE TABLE IF NOT EXISTS node_activity_map (
            node_id TEXT PRIMARY KEY,
            polyglot_active INTEGER NOT NULL DEFAULT 0,
            rest_seen INTEGER NOT NULL DEFAULT 0,
            last_source TEXT,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL
        )
        """
    )

    _ensure_node_activity_map_schema(cursor)

    _ensure_events_dynamic_numeric_schema(cursor)

    # Recreate indexes after potential table rebuild.
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
    uom_label: str | None = None,
    source: str | None = None,
    enum_value: int | None = None,
    enum_text: str | None = None,
    event_time_ms: int | None = None,
    editor_id: str | None = None,
    nls_prefix: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    allowed_subset: list[str] | None = None,
    enum_map: dict[str, str] | None = None,
):
    if not node_id or not control:
        return

    seen_ms = event_time_ms if event_time_ms is not None else _now_ms()
    is_timestamp_like, storage_policy, policy_reason = _derive_storage_policy(_coerce_int(uom))

    conn = _connect()
    cursor = conn.cursor()
    allowed_subset_id, allowed_subset_json = _get_or_create_allowed_subset_id(cursor, allowed_subset)
    enum_map_json = json.dumps(enum_map, separators=(",", ":")) if enum_map else None
    cursor.execute(
        """
        INSERT INTO node_control_static (
            node_id,
            control,
            name,
            action,
            uom,
            enum_map_json,
            uom_label,
            source,
            is_timestamp_like,
            storage_policy,
            policy_reason,
            first_seen_ms,
            last_seen_ms,
            refreshed_at_ms,
            editor_id,
            nls_prefix,
            min_value,
            max_value,
            allowed_subset_json,
            allowed_subset_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id, control)
        DO UPDATE SET
            name = COALESCE(excluded.name, node_control_static.name),
            action = COALESCE(excluded.action, node_control_static.action),
            uom = COALESCE(excluded.uom, node_control_static.uom),
            enum_map_json = COALESCE(excluded.enum_map_json, node_control_static.enum_map_json),
            uom_label = COALESCE(excluded.uom_label, node_control_static.uom_label),
            source = COALESCE(excluded.source, node_control_static.source),
            editor_id = COALESCE(excluded.editor_id, node_control_static.editor_id),
            nls_prefix = COALESCE(excluded.nls_prefix, node_control_static.nls_prefix),
            min_value = COALESCE(excluded.min_value, node_control_static.min_value),
            max_value = COALESCE(excluded.max_value, node_control_static.max_value),
            allowed_subset_json = COALESCE(excluded.allowed_subset_json, node_control_static.allowed_subset_json),
            allowed_subset_id = COALESCE(excluded.allowed_subset_id, node_control_static.allowed_subset_id),
            is_timestamp_like = excluded.is_timestamp_like,
            storage_policy = excluded.storage_policy,
            policy_reason = excluded.policy_reason,
            last_seen_ms = CASE
                WHEN excluded.last_seen_ms > node_control_static.last_seen_ms THEN excluded.last_seen_ms
                ELSE node_control_static.last_seen_ms
            END,
            refreshed_at_ms = excluded.refreshed_at_ms
        """,
        (
            node_id,
            control,
            name,
            action,
            uom,
            enum_map_json,
            uom_label,
            source,
            is_timestamp_like,
            storage_policy,
            policy_reason,
            seen_ms,
            seen_ms,
            seen_ms,
            editor_id,
            nls_prefix,
            min_value,
            max_value,
            allowed_subset_json,
            allowed_subset_id,
        ),
    )

    cursor.execute(
        """
        SELECT
            ncs.node_id,
            ncs.control,
            ncs.name,
            ncs.action,
            ncs.uom,
            ncs.enum_map_json,
            ncs.uom_label,
            ncs.source,
            ncs.editor_id,
            ncs.nls_prefix,
            ncs.min_value,
            ncs.max_value,
            asl.subset_json AS allowed_subset_json,
            ncs.is_timestamp_like,
            ncs.storage_policy,
            ncs.policy_reason,
            ncs.first_seen_ms,
            ncs.last_seen_ms,
            ncs.refreshed_at_ms
        FROM node_control_static ncs
        LEFT JOIN allowed_subset_lookup asl ON asl.id = ncs.allowed_subset_id
        WHERE ncs.node_id = ? AND ncs.control = ?
        """,
        (node_id, control),
    )
    updated_row = cursor.fetchone()

    conn.commit()
    conn.close()
    if updated_row is None:
        return None
    return _to_control_meta(updated_row)


def bulk_upsert_static_metadata(
    records: list[dict[str, Any]],
    event_time_ms: int | None = None,
) -> int:
    """Upsert many node/control metadata rows in one transaction.

    This is used by REST startup refresh where many rows are inserted at once.
    """
    if not records:
        return 0

    seen_ms = event_time_ms if event_time_ms is not None else _now_ms()
    conn = _connect()
    cursor = conn.cursor()
    applied = 0

    try:
        for rec in records:
            node_id = str(rec.get("node_id") or "").strip()
            control = str(rec.get("control") or "").strip()
            if not node_id or not control:
                continue

            uom = rec.get("uom")
            is_timestamp_like, storage_policy, policy_reason = _derive_storage_policy(_coerce_int(uom))
            allowed_subset_id, allowed_subset_json = _get_or_create_allowed_subset_id(cursor, rec.get("allowed_subset"))
            enum_map = rec.get("enum_map")
            enum_map_json = json.dumps(enum_map, separators=(",", ":")) if enum_map else None

            cursor.execute(
                """
                INSERT INTO node_control_static (
                    node_id,
                    control,
                    name,
                    action,
                    uom,
                    enum_map_json,
                    uom_label,
                    source,
                    is_timestamp_like,
                    storage_policy,
                    policy_reason,
                    first_seen_ms,
                    last_seen_ms,
                    refreshed_at_ms,
                    editor_id,
                    nls_prefix,
                    min_value,
                    max_value,
                    allowed_subset_json,
                    allowed_subset_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id, control)
                DO UPDATE SET
                    name = COALESCE(excluded.name, node_control_static.name),
                    action = COALESCE(excluded.action, node_control_static.action),
                    uom = COALESCE(excluded.uom, node_control_static.uom),
                    enum_map_json = COALESCE(excluded.enum_map_json, node_control_static.enum_map_json),
                    uom_label = COALESCE(excluded.uom_label, node_control_static.uom_label),
                    source = COALESCE(excluded.source, node_control_static.source),
                    editor_id = COALESCE(excluded.editor_id, node_control_static.editor_id),
                    nls_prefix = COALESCE(excluded.nls_prefix, node_control_static.nls_prefix),
                    min_value = COALESCE(excluded.min_value, node_control_static.min_value),
                    max_value = COALESCE(excluded.max_value, node_control_static.max_value),
                    allowed_subset_json = COALESCE(excluded.allowed_subset_json, node_control_static.allowed_subset_json),
                    allowed_subset_id = COALESCE(excluded.allowed_subset_id, node_control_static.allowed_subset_id),
                    is_timestamp_like = excluded.is_timestamp_like,
                    storage_policy = excluded.storage_policy,
                    policy_reason = excluded.policy_reason,
                    last_seen_ms = CASE
                        WHEN excluded.last_seen_ms > node_control_static.last_seen_ms THEN excluded.last_seen_ms
                        ELSE node_control_static.last_seen_ms
                    END,
                    refreshed_at_ms = excluded.refreshed_at_ms
                """,
                (
                    node_id,
                    control,
                    rec.get("name"),
                    rec.get("action"),
                    uom,
                    enum_map_json,
                    rec.get("uom_label"),
                    rec.get("source"),
                    is_timestamp_like,
                    storage_policy,
                    policy_reason,
                    seen_ms,
                    seen_ms,
                    seen_ms,
                    rec.get("editor_id"),
                    rec.get("nls_prefix"),
                    rec.get("min_value"),
                    rec.get("max_value"),
                    allowed_subset_json,
                    allowed_subset_id,
                ),
            )

            applied += 1

        conn.commit()
        return applied
    finally:
        conn.close()


def load_control_metadata_index() -> dict[tuple[str, str], dict[str, Any]]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            ncs.node_id,
            ncs.control,
            ncs.name,
            ncs.action,
            ncs.uom,
            ncs.enum_map_json,
            ncs.uom_label,
            ncs.source,
            ncs.editor_id,
            ncs.nls_prefix,
            ncs.min_value,
            ncs.max_value,
            asl.subset_json AS allowed_subset_json,
            ncs.is_timestamp_like,
            ncs.storage_policy,
            ncs.policy_reason,
            ncs.first_seen_ms,
            ncs.last_seen_ms,
            ncs.refreshed_at_ms
        FROM node_control_static ncs
        LEFT JOIN allowed_subset_lookup asl ON asl.id = ncs.allowed_subset_id
        """
    )
    rows = cursor.fetchall()
    conn.close()

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        node_id = str(row["node_id"])
        control = str(row["control"])
        out[(node_id, control)] = _to_control_meta(row)
    return out


def insert_dynamic_event(
    node_id: str,
    control: str,
    value: Any,
    event_time_ms: int | None,
):
    if not node_id or not control:
        return

    event_ms = event_time_ms if event_time_ms is not None else _now_ms()
    value_num = _coerce_float(value)
    if value_num is None:
        LOGGER.warning("Skipping non-numeric dynamic value: node=%s control=%s value=%s", node_id, control, value)
        return

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO events_dynamic (
            event_time_ms,
            node_id,
            control,
            value
        )
        VALUES (?, ?, ?, ?)
        """,
        (event_ms, node_id, control, value_num),
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


def upsert_node_activity(
    node_id: str,
    *,
    polyglot_active: bool | None = None,
    rest_seen: bool | None = None,
    source: str | None = None,
    seen_ms: int | None = None,
):
    node_key = str(node_id or "").strip()
    if not node_key:
        return

    mark_ms = seen_ms if seen_ms is not None else _now_ms()
    poly_value = None if polyglot_active is None else (1 if polyglot_active else 0)
    rest_value = None if rest_seen is None else (1 if rest_seen else 0)

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO node_activity_map (
            node_id,
            polyglot_active,
            rest_seen,
            last_source,
            first_seen_ms,
            last_seen_ms
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id)
        DO UPDATE SET
            polyglot_active = CASE
                WHEN ? IS NULL THEN node_activity_map.polyglot_active
                ELSE ?
            END,
            rest_seen = CASE
                WHEN ? IS NULL THEN node_activity_map.rest_seen
                ELSE ?
            END,
            last_source = COALESCE(excluded.last_source, node_activity_map.last_source),
            last_seen_ms = CASE
                WHEN excluded.last_seen_ms > node_activity_map.last_seen_ms THEN excluded.last_seen_ms
                ELSE node_activity_map.last_seen_ms
            END
        """,
        (
            node_key,
            0 if poly_value is None else poly_value,
            0 if rest_value is None else rest_value,
            source,
            mark_ms,
            mark_ms,
            poly_value,
            0 if poly_value is None else poly_value,
            rest_value,
            0 if rest_value is None else rest_value,
        ),
    )

    conn.commit()
    conn.close()


def bulk_upsert_node_activity(
    node_ids: list[str],
    *,
    polyglot_active: bool | None = None,
    rest_seen: bool | None = None,
    source: str | None = None,
    seen_ms: int | None = None,
) -> int:
    if not node_ids:
        return 0

    mark_ms = seen_ms if seen_ms is not None else _now_ms()
    conn = _connect()
    cursor = conn.cursor()
    applied = 0

    poly_value = None if polyglot_active is None else (1 if polyglot_active else 0)
    rest_value = None if rest_seen is None else (1 if rest_seen else 0)

    try:
        for raw_node_id in node_ids:
            node_id = str(raw_node_id or "").strip()
            if not node_id:
                continue
            cursor.execute(
                """
                INSERT INTO node_activity_map (
                    node_id,
                    polyglot_active,
                    rest_seen,
                    last_source,
                    first_seen_ms,
                    last_seen_ms
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id)
                DO UPDATE SET
                    polyglot_active = CASE
                        WHEN ? IS NULL THEN node_activity_map.polyglot_active
                        ELSE ?
                    END,
                    rest_seen = CASE
                        WHEN ? IS NULL THEN node_activity_map.rest_seen
                        ELSE ?
                    END,
                    last_source = COALESCE(excluded.last_source, node_activity_map.last_source),
                    last_seen_ms = CASE
                        WHEN excluded.last_seen_ms > node_activity_map.last_seen_ms THEN excluded.last_seen_ms
                        ELSE node_activity_map.last_seen_ms
                    END
                """,
                (
                    node_id,
                    0 if poly_value is None else poly_value,
                    0 if rest_value is None else rest_value,
                    source,
                    mark_ms,
                    mark_ms,
                    poly_value,
                    0 if poly_value is None else poly_value,
                    rest_value,
                    0 if rest_value is None else rest_value,
                ),
            )
            applied += 1
        conn.commit()
        return applied
    finally:
        conn.close()


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


