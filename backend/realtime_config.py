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

if HALF_LIFE_MINUTES <= 0:
    HALF_LIFE_MINUTES = 120.0
if SWEEP_SECONDS <= 0:
    SWEEP_SECONDS = 30.0
