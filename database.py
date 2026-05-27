
import sqlite3
from datetime import datetime

DB_NAME = "history.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            node_id TEXT,
            value REAL,
            uom INTEGER
        )
    """)
    conn.commit()
    conn.close()

def log_event(node_id, value, uom=2):
    try:
        val_float = float(value)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO device_logs (timestamp, node_id, value, uom)
            VALUES (?, ?, ?, ?)
        """, (datetime.now().isoformat(), node_id, val_float, uom))
        conn.commit()
        conn.close()
    except ValueError:
        pass # Skip non-numeric data that can't be computed by ML