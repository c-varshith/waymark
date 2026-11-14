"""Pure, clock-injected decay and aggregation for operational incident signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    corroboration_window_minutes: float = config.CORROBORATION_WINDOW_MIN
    corroboration_bonus: float = config.CORROBORATION_BONUS
    corroboration_max: float = config.CORROBORATION_MAX


DEFAULT_CONFIG = OverlayConfig(
    incident_points=config.INCIDENT_POINTS,
    status_weight=config.STATUS_WEIGHT,
    half_life_minutes=config.HALF_LIFE_MINUTES,
    min_effective_points=config.MIN_EFFECTIVE_POINTS,
    max_age_minutes=config.MAX_AGE_MINUTES,
    pending_max_age_minutes=config.PENDING_MAX_AGE_MINUTES,
    overlay_cap=config.OVERLAY_CAP,
    corroboration_window_minutes=config.CORROBORATION_WINDOW_MIN,
    corroboration_bonus=config.CORROBORATION_BONUS,
    corroboration_max=config.CORROBORATION_MAX,
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


def aggregate_details(items: Iterable[Any], now: datetime, cfg: OverlayConfig = DEFAULT_CONFIG
                      ) -> dict[str, tuple[float, int, int, float]]:
    """Return capped points, effective reports, independent reporters, and bonus points by cell."""
    grouped: dict[str, list[tuple[float, datetime, Any]]] = {}
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
        grouped.setdefault(cell_id, []).append((points, _utc(_field(item, "occurred_at")),
                                                 _field(item, "reporter_id")))
    totals: dict[str, tuple[float, int, int, float]] = {}
    for cell_id, reports in grouped.items():
        base_total = sum(points for points, _occurred, _reporter in reports)
        cutoff = _utc(now) - timedelta(minutes=max(0.0, cfg.corroboration_window_minutes))
        recent = sorted((row for row in reports if row[1] >= cutoff), key=lambda row: row[1])
        reporters: set[str] = set()
        bonus = 0.0
        for points, _occurred, reporter in recent:
            if reporter is None:
                continue
            reporter_key = str(reporter)
            if reporter_key in reporters:
                continue
            reporters.add(reporter_key)
            if len(reporters) > 1:
                bonus += points * max(0.0, cfg.corroboration_bonus)
        independent = max(0, len(reporters) - 1)
        bonus = min(bonus, max(0.0, base_total * (cfg.corroboration_max - 1.0)))
        total = min(cfg.overlay_cap, base_total + bonus)
        totals[cell_id] = (total, len(reports), independent, bonus)
    return totals


def aggregate(items: Iterable[Any], now: datetime, cfg: OverlayConfig = DEFAULT_CONFIG
              ) -> dict[str, tuple[float, int]]:
    """Backward-compatible points/count view of aggregate_details."""
    return {cell_id: (points, count) for cell_id, (points, count, _independent, _bonus)
            in aggregate_details(items, now, cfg).items()}


def overlay_params(cfg: OverlayConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    return {
        "half_life_minutes": cfg.half_life_minutes,
        "status_weights": dict(cfg.status_weight),
        "min_effective_points": cfg.min_effective_points,
        "corroboration_window_minutes": cfg.corroboration_window_minutes,
        "corroboration_bonus": cfg.corroboration_bonus,
        "corroboration_max": cfg.corroboration_max,
    }
