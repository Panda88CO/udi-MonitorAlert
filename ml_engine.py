import logging
import math
from typing import Any
import database

try:
    from udi_interface import LOGGER
except ImportError:
    LOGGER = logging.getLogger(__name__)

DEFAULT_Z_THRESHOLD = 3.0
DEFAULT_MIN_SAMPLES = 10
DEFAULT_BASELINE_WINDOW_MS = 30 * 24 * 60 * 60 * 1000  # 30 days


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_z_score(value: float, mean: float, stddev: float) -> float:
    """Calculate the absolute Z-score for a value against mean and stddev."""
    if stddev <= 1e-6:
        return 0.0
    return abs(value - mean) / stddev


def calculate_anomaly_score(z_score: float, threshold: float = DEFAULT_Z_THRESHOLD) -> int:
    """Map a Z-score >= threshold into an anomaly score between 80 and 100."""
    if z_score < threshold:
        return 0
    # Linear ramp from 80 at threshold to 100 at threshold + 4 standard deviations
    excess = z_score - threshold
    scaled = 80 + int(min(20.0, (excess / 4.0) * 20))
    return min(100, max(80, scaled))


def analyze_datapoint(
    node_id: str,
    new_value: Any,
    control: str = "ST",
    event_time_ms: int | None = None,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    check_spikes: bool = True,
    baseline_window_ms: int | None = DEFAULT_BASELINE_WINDOW_MS,
) -> tuple[bool, int, dict[str, Any]]:
    """Analyze a single datapoint against SQLite statistical baselines.

    Args:
        node_id: Device address / identifier.
        new_value: Raw sensor value.
        control: Control key (e.g. 'ST', 'CLITEMP', 'CLIHUM', 'BATLVL').
        event_time_ms: Timestamp of the event in milliseconds.
        z_threshold: Number of standard deviations to trigger an anomaly (default 3.0).
        min_samples: Minimum historical points required before evaluation (default 10).
        check_spikes: Whether to check for sudden step-change rate spikes.
        baseline_window_ms: Historical time window for baseline calculation.

    Returns:
        (is_anomaly: bool, score: int, details: dict)
    """
    val = _coerce_float(new_value)
    if val is None:
        return False, 0, {"reason": "non_numeric_value"}

    if not node_id or not control:
        return False, 0, {"reason": "missing_node_or_control"}

    # 1. Rate-of-change / Step-jump check against previous reading
    if check_spikes and event_time_ms is not None:
        last_event = database.get_last_event(node_id, control)
        if last_event:
            prev_val = _coerce_float(last_event.get("value"))
            prev_time = last_event.get("event_time_ms")
            if prev_val is not None and prev_time is not None:
                delta_ms = event_time_ms - prev_time
                # If reading occurred within 60s and jumped abnormally
                if 0 < delta_ms <= 60000:
                    delta_val = abs(val - prev_val)
                    delta_sec = delta_ms / 1000.0
                    rate = delta_val / delta_sec
                    # Flag massive sudden jump > 50 units with rapid rate
                    if delta_val >= 50.0 and rate >= 5.0:
                        score = min(100, int(85 + min(15, rate)))
                        return True, score, {
                            "type": "step_spike",
                            "delta_value": round(delta_val, 2),
                            "delta_seconds": round(delta_sec, 2),
                            "rate_per_second": round(rate, 2),
                            "prev_value": prev_val,
                            "current_value": val,
                        }

    # 2. SQLite statistical baseline analysis
    baseline = database.get_sensor_baseline(
        node_id, control, window_ms=baseline_window_ms, current_time_ms=event_time_ms
    )
    if not baseline:
        return False, 0, {"reason": "no_baseline_data"}

    count = baseline.get("count", 0)
    if count < min_samples:
        return False, 0, {
            "reason": "insufficient_samples",
            "count": count,
            "min_samples": min_samples,
        }

    mean = baseline.get("mean", 0.0)
    stddev = baseline.get("stddev", 0.0)

    if stddev <= 1e-6:
        # If all past values were identical, check if new value deviates
        if abs(val - mean) > 1e-4:
            return True, 85, {
                "type": "zero_variance_deviation",
                "mean": mean,
                "current_value": val,
                "sample_count": count,
            }
        return False, 0, {"reason": "zero_variance_normal"}

    z = calculate_z_score(val, mean, stddev)
    if z >= z_threshold:
        score = calculate_anomaly_score(z, threshold=z_threshold)
        return True, score, {
            "type": "z_score",
            "z_score": round(z, 2),
            "threshold": z_threshold,
            "mean": round(mean, 2),
            "stddev": round(stddev, 2),
            "min_val": round(baseline.get("min", 0.0), 2),
            "max_val": round(baseline.get("max", 0.0), 2),
            "sample_count": count,
            "current_value": val,
        }

    return False, 0, {
        "reason": "normal",
        "z_score": round(z, 2),
        "mean": round(mean, 2),
        "stddev": round(stddev, 2),
        "sample_count": count,
    }


def _is_task_in_cooldown(task: dict[str, Any], now_ms: int) -> bool:
    last_trig = task.get("last_triggered_ms")
    if not last_trig:
        return False
    cooldown = task.get("cooldown_ms", 3600000)
    return (now_ms - last_trig) < cooldown


def evaluate_live_event_tasks(
    node_id: str,
    control: str,
    new_value: Any,
    event_time_ms: int | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate live event against configured monitor tasks (Spike, Contextual Hourly, Threshold).

    Returns list of triggered anomaly dicts.
    """
    val = _coerce_float(new_value)
    if val is None or not node_id or not control:
        return []

    now_ms = event_time_ms if event_time_ms is not None else int(database._now_ms())
    active_tasks = tasks if tasks is not None else database.load_active_monitor_tasks()
    triggered = []

    # Filter tasks matching node_id and control
    matching_tasks = [
        t for t in active_tasks
        if database._match_pattern(node_id, t.get("node_id_pattern", "*"))
        and database._match_pattern(control, t.get("control_pattern", "*"))
        and t.get("task_type") in ("spike", "contextual_hourly", "threshold")
    ]

    for task in matching_tasks:
        task_id = task["task_id"]
        task_type = task["task_type"]
        params = task.get("params") or {}

        if _is_task_in_cooldown(task, now_ms):
            continue

        if task_type == "spike":
            z_thresh = float(params.get("z_threshold", DEFAULT_Z_THRESHOLD))
            min_samples = int(params.get("min_samples", DEFAULT_MIN_SAMPLES))
            check_spikes = bool(params.get("check_spikes", True))

            is_anom, score, details = analyze_datapoint(
                node_id=node_id,
                new_value=val,
                control=control,
                event_time_ms=now_ms,
                z_threshold=z_thresh,
                min_samples=min_samples,
                check_spikes=check_spikes,
            )

            # Check static ceiling threshold if specified in params
            max_val = params.get("max_value")
            if max_val is not None and val > float(max_val):
                is_anom = True
                score = max(score, 90)
                details["type"] = "max_threshold_exceeded"
                details["max_threshold"] = float(max_val)
                details["current_value"] = val

            if is_anom:
                database.record_task_triggered(task_id, now_ms)
                triggered.append({
                    "task_id": task_id,
                    "task_name": task.get("name") or task_id,
                    "task_type": "spike",
                    "severity": task.get("severity", "warning"),
                    "node_id": node_id,
                    "control": control,
                    "value": val,
                    "score": score,
                    "details": details,
                    "timestamp_ms": now_ms,
                })

        elif task_type == "contextual_hourly":
            z_thresh = float(params.get("z_threshold", DEFAULT_Z_THRESHOLD))
            min_samples = int(params.get("min_samples", 5))
            window_days = int(params.get("window_days", 30))

            # Derive hour of day (0-23) in UTC
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
            hour = dt.hour

            baseline = database.get_hourly_sensor_baseline(
                node_id=node_id,
                control=control,
                hour_of_day=hour,
                window_days=window_days,
                current_time_ms=now_ms,
            )

            if baseline and baseline.get("count", 0) >= min_samples and baseline.get("stddev", 0.0) > 1e-4:
                mean = baseline["mean"]
                std = baseline["stddev"]
                z = calculate_z_score(val, mean, std)
                if z >= z_thresh:
                    score = calculate_anomaly_score(z, threshold=z_thresh)
                    database.record_task_triggered(task_id, now_ms)
                    triggered.append({
                        "task_id": task_id,
                        "task_name": task.get("name") or task_id,
                        "task_type": "contextual_hourly",
                        "severity": task.get("severity", "warning"),
                        "node_id": node_id,
                        "control": control,
                        "value": val,
                        "score": score,
                        "details": {
                            "hour_of_day": hour,
                            "z_score": round(z, 2),
                            "hourly_mean": round(mean, 2),
                            "hourly_std": round(std, 2),
                            "sample_count": baseline["count"],
                        },
                        "timestamp_ms": now_ms,
                    })

        elif task_type == "threshold":
            min_val = params.get("min_value")
            max_val = params.get("max_value")
            violation = False
            reason = ""
            if min_val is not None and val < float(min_val):
                violation = True
                reason = f"value {val} below minimum threshold {min_val}"
            elif max_val is not None and val > float(max_val):
                violation = True
                reason = f"value {val} above maximum threshold {max_val}"

            if violation:
                database.record_task_triggered(task_id, now_ms)
                triggered.append({
                    "task_id": task_id,
                    "task_name": task.get("name") or task_id,
                    "task_type": "threshold",
                    "severity": task.get("severity", "warning"),
                    "node_id": node_id,
                    "control": control,
                    "value": val,
                    "score": 85,
                    "details": {"reason": reason, "current_value": val},
                    "timestamp_ms": now_ms,
                })

    # If no explicit tasks were defined for this sensor, perform default autonomous spike check
    if not matching_tasks:
        is_anom, score, details = analyze_datapoint(
            node_id=node_id,
            new_value=val,
            control=control,
            event_time_ms=now_ms,
        )
        if is_anom:
            triggered.append({
                "task_id": f"auto_{node_id}_{control}",
                "task_name": f"Autonomous Outlier ({node_id})",
                "task_type": "autonomous_spike",
                "severity": "warning",
                "node_id": node_id,
                "control": control,
                "value": val,
                "score": score,
                "details": details,
                "timestamp_ms": now_ms,
            })

    return triggered


def evaluate_periodic_tasks(
    tasks: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Evaluate periodic/scheduled tasks (Stuck Node Watchdogs and Slow Creep/Leak detection).

    Returns list of triggered anomaly dicts.
    """
    current_ms = now_ms if now_ms is not None else int(database._now_ms())
    active_tasks = tasks if tasks is not None else database.load_active_monitor_tasks()
    triggered = []

    for task in active_tasks:
        task_id = task["task_id"]
        task_type = task["task_type"]
        params = task.get("params") or {}
        node_pattern = task.get("node_id_pattern", "*")
        ctrl_pattern = task.get("control_pattern", "*")

        if _is_task_in_cooldown(task, current_ms):
            continue

        if task_type == "stuck_watchdog":
            max_silent_minutes = float(params.get("max_silent_minutes", 120))
            max_silent_ms = int(max_silent_minutes * 60000)

            stuck_nodes = database.check_stuck_nodes_query(
                node_pattern=node_pattern,
                control_pattern=ctrl_pattern,
                max_silent_ms=max_silent_ms,
                now_ms=current_ms,
            )

            for item in stuck_nodes:
                database.record_task_triggered(task_id, current_ms)
                triggered.append({
                    "task_id": task_id,
                    "task_name": task.get("name") or task_id,
                    "task_type": "stuck_watchdog",
                    "severity": task.get("severity", "warning"),
                    "node_id": item["node_id"],
                    "control": item["control"],
                    "value": item.get("last_value"),
                    "score": 90,
                    "details": {
                        "silent_minutes": round(item["silent_minutes"], 1),
                        "max_allowed_minutes": max_silent_minutes,
                        "last_seen_ms": item["last_seen_ms"],
                    },
                    "timestamp_ms": current_ms,
                })
                break  # Record one alert per task run

        elif task_type == "slow_creep":
            window_minutes = int(params.get("window_minutes", 180))
            max_zero_threshold = float(params.get("max_zero_threshold", 0.05))
            min_samples = int(params.get("min_samples", 5))

            window_start_ms = current_ms - (window_minutes * 60000)
            window_end_ms = current_ms

            # Query single target node if pattern is exact, or matching nodes
            result = database.check_slow_creep_query(
                node_id=node_pattern,
                control=ctrl_pattern,
                window_start_ms=window_start_ms,
                window_end_ms=window_end_ms,
                max_zero_threshold=max_zero_threshold,
                min_samples=min_samples,
            )

            if result:
                database.record_task_triggered(task_id, current_ms)
                triggered.append({
                    "task_id": task_id,
                    "task_name": task.get("name") or task_id,
                    "task_type": "slow_creep",
                    "severity": task.get("severity", "warning"),
                    "node_id": node_pattern,
                    "control": ctrl_pattern,
                    "value": result["min_value"],
                    "score": 85,
                    "details": {
                        "min_value_observed": result["min_value"],
                        "threshold": max_zero_threshold,
                        "sample_count": result["sample_count"],
                        "window_minutes": window_minutes,
                    },
                    "timestamp_ms": current_ms,
                })

    return triggered