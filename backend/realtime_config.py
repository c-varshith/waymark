"""Operational thresholds shared by WAYMARK live inference surfaces."""
from __future__ import annotations

import os


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _rush_windows() -> tuple[tuple[int, int], ...]:
    raw = os.getenv("WAYMARK_RUSH_WINDOWS", "07-10,16-19")
    try:
        parsed = tuple(tuple(int(part) for part in pair.split("-", 1))
                       for pair in raw.split(",") if pair.strip())
        if parsed and all(len(pair) == 2 and 0 <= pair[0] < pair[1] <= 24 for pair in parsed):
            return parsed
    except (TypeError, ValueError):
        pass
    return ((7, 10), (16, 19))


STALE_AFTER_SECONDS = _int("WAYMARK_STALE_AFTER_SECONDS", 120)
HEARTBEAT_SECONDS = _int("WAYMARK_HEARTBEAT_SECONDS", 20)
STREAM_MAX_SUBSCRIBERS = _int("WAYMARK_STREAM_MAX_SUBSCRIBERS", 200)
REPLAY_BUFFER = _int("WAYMARK_REPLAY_BUFFER", 200)
CLIENT_WATCHDOG_SECONDS = _int("WAYMARK_CLIENT_WATCHDOG_SECONDS", 45)
POLL_FALLBACK_SECONDS = _int("WAYMARK_POLL_FALLBACK_SECONDS", 30)

INCIDENT_POINTS = {
    "crash": _float("WAYMARK_CRASH_POINTS", 20.0),
    "near_miss": _float("WAYMARK_NEAR_MISS_POINTS", 10.0),
}
STATUS_WEIGHT = {
    "pending": _float("WAYMARK_PENDING_WEIGHT", 0.6),
    "confirmed": _float("WAYMARK_CONFIRMED_WEIGHT", 1.0),
    "rejected": _float("WAYMARK_REJECTED_WEIGHT", 0.0),
}
HALF_LIFE_MINUTES = _float("WAYMARK_HALF_LIFE_MINUTES", 120.0)
MIN_EFFECTIVE_POINTS = _float("WAYMARK_MIN_EFFECTIVE_POINTS", 1.0)
MAX_AGE_MINUTES = _float("WAYMARK_MAX_AGE_MINUTES", 720.0)
PENDING_MAX_AGE_MINUTES = _float("WAYMARK_PENDING_MAX_AGE_MINUTES", 240.0)
OVERLAY_CAP = _float("WAYMARK_OVERLAY_CAP", 100.0)
SWEEP_SECONDS = _float("WAYMARK_SWEEP_SECONDS", 30.0)
SWEEP_PUBLISH_DELTA = _float("WAYMARK_SWEEP_PUBLISH_DELTA", 1.0)

# Shared condition definitions. Historical tagging, validation and live inference import
# these values so the same condition means the same thing in every path.
RAIN_MM = _float("WAYMARK_RAIN_MM", 1.0)
RAIN_EXIT_MM = _float("WAYMARK_RAIN_EXIT_MM", 0.5)
RUSH_WINDOWS = _rush_windows()  # local-hour half-open ranges, weekdays only
HOUSTON_TIMEZONE = os.getenv("WAYMARK_HOUSTON_TIMEZONE", "America/Chicago")
LIFT_PRIOR_STRENGTH = _float("WAYMARK_LIFT_PRIOR_STRENGTH", 10.0)
LIFT_MIN_CRASHES = _int("WAYMARK_LIFT_MIN_CRASHES", 8)
LIFT_MIN_RHO = _float("WAYMARK_LIFT_MIN_RHO", 1.3)
LIFT_STRENGTH = {
    "rain": _float("WAYMARK_LIFT_STRENGTH_RAIN", 0.5),
    "night": _float("WAYMARK_LIFT_STRENGTH_NIGHT", 0.5),
    "rush": _float("WAYMARK_LIFT_STRENGTH_RUSH", 0.5),
}
LIFT_CAP_PER_CONDITION = _float("WAYMARK_LIFT_CAP_PER_CONDITION", 1.5)
LIFT_CAP_TOTAL = _float("WAYMARK_LIFT_CAP_TOTAL", 1.75)
CONDITION_LIFT_APPLIES_TO_RANKING = _flag("WAYMARK_CONDITION_LIFT_APPLIES_TO_RANKING", False)
ALERT_HYSTERESIS = _float("WAYMARK_ALERT_HYSTERESIS", 3.0)
ALERT_THRESHOLD = _float("WAYMARK_ALERT_THRESHOLD", 80.0)

if HALF_LIFE_MINUTES <= 0:
    HALF_LIFE_MINUTES = 120.0
if SWEEP_SECONDS <= 0:
    SWEEP_SECONDS = 30.0
