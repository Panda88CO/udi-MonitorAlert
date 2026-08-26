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