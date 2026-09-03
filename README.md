# UDI MonitorAlert

A high-performance Python Polyglot (PG3x) node server for Universal Devices eISY / IoX. It monitors device event changes via NuCore or IoX WebSocket streams, stores structured event history in SQLite, and performs real-time and historical anomaly/outlier detection directly in the database.

---

## Key Features

- **Dual Event Ingestion**: NuCore callback integration (primary) with automatic fallback to native IoX WebSocket stream (`/rest/subscribe`).
- **In-Database Analytics**: Fast, native C statistical baseline computation in SQLite ($\mu$, $\sigma$, $Z$-score, step/velocity spikes) with zero memory overhead on eISY.
- **Real-Time Anomaly Scoring**: Live evaluation of incoming sensor telemetry against sliding-window baselines and sudden rate spikes.
- **Rich Profile & Schema Sync**: Automatically discovers node definitions, editor ranges, UOMs, and NLS enum maps from `/rest/status` and `/rest/profiles`.
- **Flexible Event Filtering & Persistence**: Append-only JSON Lines logging (`event_callback.jsonl`) and indexed SQLite storage (`history.db`).
- **Interactive Reporting CLI**: Built-in CLI report tool (`import sqlite3.py`) to inspect active sensor baselines, top $Z$-score anomalies, and step spikes.

---

## Architecture Flow

```
+-----------------------------------------------------------------+
|                       Universal Devices eISY                    |
|                                                                 |
|   +--------------------------+      +-----------------------+   |
|   |    NuCore Event Engine   |  or  |   IoX Event Stream    |   |
|   |    (Shared Features)     |      |   (/rest/subscribe)   |   |
|   +-------------+------------+      +-----------+-----------+   |
|                 |                               |               |
|                 +---------------+---------------+               |
|                                 |                               |
|                                 v                               |
|                   +---------------------------+                 |
|                   |       udiMonitor.py       |                 |
|                   |  (Controller: ML_CTRL)   |                 |
|                   +-------------+-------------+                 |
|                                 |                               |
|                 +---------------+---------------+               |
|                 |                               |               |
|                 v                               v               |
|   +---------------------------+   +---------------------------+ |
|   |       ml_engine.py        |   |        database.py        | |
|   |  - Real-time Z-Score eval |<->|  - SQLite (history.db)    | |
|   |  - Step/Velocity spikes   |   |  - Indexed Baselines      | |
|   |  - Confidence (80-100%)   |   |  - Profile & Event Tables | |
|   +---------------------------+   +---------------------------+ |
+-----------------------------------------------------------------+
```

---

## SQLite Database Schema (`history.db`)

1. **`events_dynamic`**: Append-only dynamic sensor events.
   - `id`: Primary key (autoincrement).
   - `event_time_ms`: Unix timestamp in milliseconds.
   - `node_id`: Target device address (e.g. `n008_8b4c01000de723`).
   - `control`: Control key (e.g. `ST`, `CLITEMP`, `CLIHUM`, `BATLVL`).
   - `value`: Numeric reading (REAL).
   - *Indexes*: Composite `(node_id, control, event_time_ms)` and `(event_time_ms)`.

2. **`node_control_static`**: Static metadata per device/control pair.
   - `node_id`, `control`, `name`, `action`, `uom`, `uom_label`, `min_value`, `max_value`, `enum_map_json`, `storage_policy`, `refreshed_at_ms`.

3. **`profile_control_schema`**: Node server profile editor and range catalog materialized from IoX REST profiles.
   - `profile_slot`, `node_def_id`, `control`, `editor_id`, `range_index`, `uom`, `uom_label`, `nls_prefix`, `min_value`, `max_value`, `enum_map_json`.

4. **`event_filters`**: Configurable rule engine for allowing or blocking specific event patterns by source, node regex, or value range.

5. **`node_activity_map`**: Tracks active nodes across Polyglot discovery and REST status refreshes.

---

## Outlier & Anomaly Detection

### 1. In-Database Z-Score Baseline
Standard deviation is calculated inside SQLite native C engine using $\sigma = \sqrt{\text{AVG}(x^2) - \text{AVG}(x)^2}$:

```sql
SELECT 
    COUNT(*) AS count,
    AVG(value) AS mean,
    SQRT(AVG(value * value) - AVG(value) * AVG(value)) AS stddev,
    MIN(value) AS min_val,
    MAX(value) AS max_val
FROM events_dynamic
WHERE node_id = ? AND control = ? AND event_time_ms >= ?
```

### 2. Live Outlier Scoring (`ml_engine.py`)
- **$Z$-Score Evaluation**: If $Z = \frac{|x - \mu|}{\sigma} \ge 3.0$ and sample count $\ge 10$, an anomaly is flagged with a confidence score between $80\%$ and $100\%$.
- **Step / Velocity Spikes**: Flags sudden jumps exceeding $50$ units within $\le 60\text{ seconds}$ at a rate $\ge 5/\text{sec}$ (detects $10\times$ multiplier errors and sensor glitches).

### 3. Historical Anomaly Scanning (`database.py`)
- **`find_historical_outliers(min_z_score, min_samples, limit)`**: CTE query returning top historical anomalies across all sensors.
- **`find_historical_spikes(max_time_delta_sec, min_value_delta, limit)`**: Windowed `LAG()` query returning velocity step jumps.

---

## Tiered Dual-Retention Storage Policy

To permanently bound database size on the eISY while preserving long-term trends for critical equipment, the background watchdog executes a native SQLite cleanup once every 24 hours:

- **Unmonitored Telemetry (Default: 30 Days)**: Retains a rolling 30-day baseline buffer for all unmonitored sensors so that newly configured monitors have immediate baseline data without a "cold start". Older unmonitored data is deleted.
- **Monitored Telemetry (Default: 365 Days)**: Retains 1+ years of history for devices matching active `monitor_tasks` to capture annual seasonality and long-term degradation.
- **Total Storage Footprint**: Keeps total SQLite storage under **10–15 MB forever**.

---

## Configuration

### 1. Connecting to IoX (`customParams`)
In the PG3x dashboard under the **Configuration** tab, enter your connection credentials:

| Key | Example Value | Description |
| :--- | :--- | :--- |
| `isy_ip` | `192.168.1.240` | eISY / IoX IPv4 address |
| `isy_user` | `admin` | Admin username |
| `isy_password` | `your_password` | Admin password |
| `unmonitored_retention_days` | `30` | *(Optional)* Rolling baseline buffer for unmonitored devices |
| `monitored_retention_days` | `365` | *(Optional)* History retention for monitored devices |

---

### 2. Defining Monitored Sensors in the PG3x Configuration Tab

You can add sensors directly in the PG3x **Custom Parameters** table without writing JSON:

1. **Copy** any device variable from the IoX Admin Console (e.g. `${sys.node.n012_8b4c01000cac1a.GV1}`).
2. In the PG3x Configuration tab, click **+ Add**:
   - **Key**: Paste the copied string (`${sys.node.n012_8b4c01000cac1a.GV1}`).
   - **Value**: Type the monitor types you want (e.g. `spike, stuck`).
3. Click **Save**:
   - The node server automatically resolves `GV1` to its friendly name from SQLite metadata (e.g. `Water Temperature`).
   - The key is auto-rewritten in the table to: `n012_8b4c01000cac1a.GV1 [Water Temperature]`.
   - The active monitoring rules are created and run in SQLite.

#### Example Configuration Rows:

| Key | Value | Result |
| :--- | :--- | :--- |
| `${sys.node.n012_8b4c01000cac1a.GV1}` | `spike, stuck` | Alerts on spikes ($Z \ge 3.0$) and watchdog silence ($> 2$h). Auto-labels `[Water Temperature]`. |
| `n008_water_meter.FLOW` | `spike, creep, stuck` | Monitors water flow for burst spikes, night trickle leaks, and sensor freeze. |
| `n008_main_panel.WATTS` | `hourly, spike` | Compares power draw to that hour-of-day's baseline, and alerts on surges. |
| `n012_pool_heater.CLITEMP` | `?` | **Help mode**: Auto-populates available options into the value field. |
| `${sys.node.n012_pool_heater}` | *(any)* | **Discovery mode**: Posts a PG3x dashboard notice listing all available controls on that device. |

#### Supported Monitor Keywords:
* **`spike`**: Fast spikes, sudden velocity steps, and upper thresholds.
* **`stuck`** *(or `watchdog`)*: Frozen telemetry or disconnected device (default $> 120$ min).
* **`creep`** *(or `leak`)*: Continuous non-zero floor over quiet hours (default $> 0.05$ over 3h).
* **`hourly`**: Contextual baseline specific to the current hour of day.
* **`all`**: Enables all 4 monitor types on that parameter.
* *Optional custom parameters*: e.g. `stuck(60m)`, `creep(0.01)`, `spike(3.5z)`.

---

### 3. Notification Configuration (Email & UD Mobile)

Configure alert dispatching in the PG3x **Configuration** tab (`customParams`):

| Key | Example Value | Description |
| :--- | :--- | :--- |
| `notify_channels` | `email, udmobile` | Notification destinations (`email`, `udmobile`, `both`, or `none`) |
| `notify_email_to` | `me@example.com, alert@domain.com` | Destination email addresses (comma-separated) |
| `notify_email_from` | `alerts@mydomain.com` | *(Optional)* Sender address (defaults to `smtp_user`) |
| `smtp_host` | `smtp.gmail.com` | Outgoing SMTP mail server |
| `smtp_port` | `587` | SMTP port (`587` for TLS/STARTTLS, `465` for SSL, `25` for local relay) |
| `smtp_user` | `user@gmail.com` | SMTP authentication username / account |
| `smtp_password` | `app-specific-password` | SMTP authentication password or app password |
| `notify_udmobile_content_id` | `1` | IoX built-in notification content ID for UD Mobile push (default `1`) |
| `notify_udmobile_recipient_id` | `1` | IoX notification recipient/user ID (default `1`) |

#### Controller Node Status Drivers (`ML_CTRL`)
The controller node drivers update immediately on every anomaly, enabling native IoX Programs and UD Mobile push triggers without requiring external network connectivity:

| Driver | Description | Values |
| :--- | :--- | :--- |
| `ST` | Node Server Status | 1 = Online, 0 = Disconnected |
| `ALARM` | Anomaly Alert Flag | 0 = Normal, 1 = Active Alert |
| `GV0` | Anomaly Confidence | 0 to 100 (%) |
| `GV1` | Anomaly Type Code | 1 = Spike / Surge, 2 = Stuck / Silent, 3 = Slow Creep / Leak, 4 = Hourly Deviation |
| `GV2` | Anomalous Reading | Last sensor value that triggered the alert |

---

### 4. PG3x `customData` (Optional NuCore Provider)
To enable NuCore shared-features event streaming:

```json
{
  "eventSource": "nucore",
  "nucore": {
    "provider_path": "iox.IoXWrapper",
    "provider_init": {
      "base_url": "https://192.168.1.240",
      "username": "admin",
      "password": "YOUR_PASSWORD",
      "json_output": true,
      "prompt_format_type": "shared-features"
    }
  }
}
```

---

## Running Reports & Diagnostics

Run the interactive SQLite reporting utility to inspect active baselines and top detected outliers:

```powershell
python "import sqlite3.py"
```

Sample output:
```
================================================================================
                         SQLITE DATA & OUTLIER REPORT
================================================================================

--- 1. DATABASE TABLES & COUNTS ---
  * events_dynamic               :    9224 rows
  * node_control_static          :     910 rows
  * event_filters                :       1 rows

--- 2. TOP ACTIVE SENSORS & BASELINES (SQLite Calculated) ---
Node ID                Control    Sensor Name                Count     Mean   StdDev     Min     Max
----------------------------------------------------------------------------------------------------
n008_8b4c01000de723    CLITEMP    Sensor Temperature          1977   181.11   104.13    23.0   296.0
n008_8b4c01000de723    CLIHUM     Current Rel Humidity (%)    1735   453.06   147.62    42.0   554.0

--- 3. TOP Z-SCORE OUTLIERS (Z >= 3.0 via SQLite) ---
Timestamp               Node ID                Ctrl       Value    Mean    Std  Z-Score
---------------------------------------------------------------------------------------
2026-06-05 19:42:57 UTC n008_8b4c01000ec55a    CLITEMP     99.0   28.50  17.64    4.00z
2026-06-07 12:47:49 UTC n008_8b4c01000de723    GV17        50.0  494.04 116.23    3.82z

--- 4. TOP STEP-CHANGE SPIKES (Velocity > 5.0/sec via SQLite LAG) ---
Timestamp               Node ID                Ctrl        Prev     New    dVal   dSec  Rate/sec
------------------------------------------------------------------------------------------------
2026-06-12 03:25:57 UTC n008_8b4c01000de723    CLITEMP     26.0   261.0   235.0    0.2   1169.15
2026-07-16 08:21:33 UTC n008_8b4c01000de723    CLIHUM     522.0    52.0   470.0   10.0     46.84
================================================================================
```

---

## Running Unit Tests

Execute the full automated test suite (47 unit tests across database, ML, parsing, subscribers, tasks, and notifications):

```powershell
python -m unittest discover -v
```

---

## License

This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for details.


