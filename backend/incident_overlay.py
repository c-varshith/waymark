"""Pure, clock-injected decay and aggregation for operational incident signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import realtime_config as config


@dataclass(frozen=True, slots=True)
class OverlayConfig:
    incident_points: Mapping[str, float]
    status_weight: Mapping[str, float]
    half_life_minutes: float
    min_effective_points: float
    max_age_minutes: float
    pending_max_age_minutes: float
    overlay_cap: float


DEFAULT_CONFIG = OverlayConfig(
    incident_points=config.INCIDENT_POINTS,
    status_weight=config.STATUS_WEIGHT,
    half_life_minutes=config.HALF_LIFE_MINUTES,
    min_effective_points=config.MIN_EFFECTIVE_POINTS,
    max_age_minutes=config.MAX_AGE_MINUTES,
    pending_max_age_minutes=config.PENDING_MAX_AGE_MINUTES,
    overlay_cap=config.OVERLAY_CAP,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def max_age_for_status(status: str, cfg: OverlayConfig = DEFAULT_CONFIG) -> float:
    if status == "pending":
        return cfg.pending_max_age_minutes
    if status == "confirmed":
        return cfg.max_age_minutes
    return 0.0


def effective_points(kind: str, status: str, occurred_at: datetime, now: datetime,
                     cfg: OverlayConfig = DEFAULT_CONFIG) -> float:
    """Return bounded incident points; naive persisted timestamps are interpreted as UTC."""
    base = float(cfg.incident_points.get(kind, 0.0))
    weight = float(cfg.status_weight.get(status, 0.0))
    if base <= 0 or weight <= 0 or cfg.half_life_minutes <= 0:
        return 0.0
    age_minutes = max(0.0, (_utc(now) - _utc(occurred_at)).total_seconds() / 60.0)
    if age_minutes > max_age_for_status(status, cfg):
        return 0.0
    points = base * weight * (0.5 ** (age_minutes / cfg.half_life_minutes))
    return points if points >= cfg.min_effective_points else 0.0


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def aggregate(items: Iterable[Any], now: datetime, cfg: OverlayConfig = DEFAULT_CONFIG
              ) -> dict[str, tuple[float, int]]:
    totals: dict[str, tuple[float, int]] = {}
    for item in items:
        points = effective_points(
            str(_field(item, "kind", "")),
            str(_field(item, "status", "")),
            _field(item, "occurred_at"),
            now,
            cfg,
        )
        if points <= 0:
            continue
        cell_id = str(_field(item, "cell_id", ""))
        total, count = totals.get(cell_id, (0.0, 0))
        totals[cell_id] = (min(cfg.overlay_cap, total + points), count + 1)
    return totals


def overlay_params(cfg: OverlayConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    return {
        "half_life_minutes": cfg.half_life_minutes,
        "status_weights": dict(cfg.status_weight),
        "min_effective_points": cfg.min_effective_points,
    }
