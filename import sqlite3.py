import sqlite3
from datetime import datetime, timezone
import database


def format_time_ms(ts_ms):
    if not ts_ms:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts_ms)


def run_analysis_report():
    print("=" * 80)
    print(" " * 25 + "SQLITE DATA & OUTLIER REPORT")
    print("=" * 80)

    conn = sqlite3.connect("history.db")
    c = conn.cursor()

    # 1. Table Counts
    print("\n--- 1. DATABASE TABLES & COUNTS ---")
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in c.fetchall()]
    for t in tables:
        count = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  * {t:<28} : {count:>7} rows")

    # 2. Sensor Summary
    print("\n--- 2. TOP ACTIVE SENSORS & BASELINES (SQLite Calculated) ---")
    c.execute(
        """
        SELECT
            e.node_id,
            e.control,
            COALESCE(s.name, e.control) AS name,
            COUNT(*) AS cnt,
            ROUND(AVG(e.value), 2) AS mean_val,
            ROUND(SQRT(AVG(e.value * e.value) - AVG(e.value) * AVG(e.value)), 2) AS std_val,
            MIN(e.value) AS min_val,
            MAX(e.value) AS max_val
        FROM events_dynamic e
        LEFT JOIN node_control_static s ON e.node_id = s.node_id AND e.control = s.control
        GROUP BY e.node_id, e.control
        HAVING cnt >= 10
        ORDER BY cnt DESC
        LIMIT 10
        """
    )
    rows = c.fetchall()
    header = f"{'Node ID':<22} {'Control':<10} {'Sensor Name':<25} {'Count':>6} {'Mean':>8} {'StdDev':>8} {'Min':>7} {'Max':>7}"
    print(header)
    print("-" * len(header))
    for r in rows:
        node_id, control, name, cnt, mean, std, min_v, max_v = r
        name_trunc = (name[:22] + "...") if len(str(name)) > 25 else str(name)
        print(f"{node_id:<22} {control:<10} {name_trunc:<25} {cnt:>6} {mean:>8.2f} {std:>8.2f} {min_v:>7.1f} {max_v:>7.1f}")

    conn.close()

    # 3. Z-Score Outliers
    print("\n--- 3. TOP Z-SCORE OUTLIERS (Z >= 3.0 via SQLite) ---")
    outliers = database.find_historical_outliers(min_z_score=3.0, min_samples=15, limit=10)
    if not outliers:
        print("  No Z-score outliers found meeting threshold criteria.")
    else:
        out_hdr = f"{'Timestamp':<23} {'Node ID':<22} {'Ctrl':<8} {'Value':>7} {'Mean':>7} {'Std':>6} {'Z-Score':>8}"
        print(out_hdr)
        print("-" * len(out_hdr))
        for o in outliers:
            t_str = format_time_ms(o["event_time_ms"])
            print(f"{t_str:<23} {o['node_id']:<22} {o['control']:<8} {o['value']:>7.1f} {o['baseline_mean']:>7.2f} {o['baseline_std']:>6.2f} {o['z_score']:>7.2f}z")

    # 4. Rate-of-Change / Step Spikes
    print("\n--- 4. TOP STEP-CHANGE SPIKES (Velocity > 5.0/sec via SQLite LAG) ---")
    spikes = database.find_historical_spikes(max_time_delta_sec=60.0, min_value_delta=10.0, limit=10)
    if not spikes:
        print("  No rate-of-change spikes found.")
    else:
        spk_hdr = f"{'Timestamp':<23} {'Node ID':<22} {'Ctrl':<8} {'Prev':>7} {'New':>7} {'dVal':>7} {'dSec':>6} {'Rate/sec':>9}"
        print(spk_hdr)
        print("-" * len(spk_hdr))
        for s in spikes:
            t_str = format_time_ms(s["event_time_ms"])
            print(f"{t_str:<23} {s['node_id']:<22} {s['control']:<8} {s['prev_value']:>7.1f} {s['value']:>7.1f} {s['delta_value']:>7.1f} {s['delta_seconds']:>6.1f} {s['rate_per_second']:>9.2f}")

    # 5. Configured Monitor Tasks
    print("\n--- 5. CONFIGURED MONITOR TASKS (monitor_tasks table) ---")
    tasks = database.load_active_monitor_tasks()
    if not tasks:
        print("  No custom monitor tasks configured. (Autonomous statistical checking is active).")
    else:
        tsk_hdr = f"{'Task ID':<18} {'Type':<18} {'Node Pattern':<22} {'Ctrl':<8} {'Severity':<10}"
        print(tsk_hdr)
        print("-" * len(tsk_hdr))
        for t in tasks:
            print(f"{t['task_id']:<18} {t['task_type']:<18} {t['node_id_pattern']:<22} {t['control_pattern']:<8} {t['severity']:<10}")

    # 6. Stuck Node Watchdog Diagnostics
    print("\n--- 6. STUCK NODE WATCHDOG (Nodes silent for > 2 hours) ---")
    stuck = database.check_stuck_nodes_query(node_pattern="*", control_pattern="*", max_silent_ms=7200000)
    if not stuck:
        print("  All nodes have reported events recently.")
    else:
        stk_hdr = f"{'Node ID':<22} {'Control':<10} {'Sensor Name':<25} {'Last Value':>10} {'Silent (Hours)':>15}"
        print(stk_hdr)
        print("-" * len(stk_hdr))
        for st in stuck[:10]:
            name_trunc = (str(st['name'])[:22] + "...") if len(str(st['name'])) > 25 else str(st['name'])
            silent_hours = st['silent_minutes'] / 60.0
            print(f"{st['node_id']:<22} {st['control']:<10} {name_trunc:<25} {st['last_value'] if st['last_value'] is not None else 'N/A':>10} {silent_hours:>15.1f}h")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    run_analysis_report()