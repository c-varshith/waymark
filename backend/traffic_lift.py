"""Learned traffic-congestion effect on crash risk ("traffic lift").

The question this answers: in a given cell, is the crash rate per hour higher or lower when the
road is congested than in that cell's average hour? The answer is learned from history, checked on
a held-out year, and only then used to move live scores.

How it is learned (``fit_traffic_lift``):

* Historical traffic arrives as an hourly archive (see ``load_traffic_archive``): per H3 cell and hour,
  a congestion ratio ``1 - speed / free-flow speed``. It is bucketed with the same thresholds the live
  feed uses (free / moderate / heavy), so a learned effect means the same thing at prediction time.
* Crashes are matched to the archive by (cell, local hour). Only cell-hours the archive covers count.
* For each bucket ``b`` the citywide rate ratio is ``RR_b = crash share in b / hour share in b``.
  Exposure-weighted, the ratios average to 1, so the baseline model score stays the "average hour".
* Per cell the ratio is shrunk towards what the citywide ratios predict for that cell's own exposure
  (``prior_strength`` pseudo-crashes), then turned into a bounded multiplier
  ``1 + strength * (RR - 1)`` in ``[floor, cap]``. It can lower a score as well as raise it.
  Cells with too little history fall back to the citywide multiplier.

How it is checked (``validate_traffic_lift``): fit on years up to ``train_through``, then ask whether
the learned multipliers predict *when* crashes happened in the held-out year better than "every hour
is alike", per cell. The score is the Poisson log-likelihood gain per held-out crash with a cell
bootstrap interval. The lift is applied to live scores only when the interval's lower bound is above 0.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import realtime_config as config

log = logging.getLogger("waymark.traffic_lift")

BUCKETS = ("free", "moderate", "heavy")
SCHEMA_VERSION = 1


# ------------------------------------------------------------------ inputs
def ratio_bucket(ratio: np.ndarray) -> np.ndarray:
    """Vectorised twin of traffic.congestion_bucket, same thresholds as the live feed."""
    return np.where(ratio < config.TRAFFIC_FREE_MAX, "free",
                    np.where(ratio < config.TRAFFIC_MODERATE_MAX, "moderate", "heavy"))


def _local_hours(values: pd.Series) -> pd.Series:
    """Parse timestamps to naive local (Houston) hours; zone-aware values are converted first."""
    parsed = pd.to_datetime(values, errors="coerce", utc=False, format="mixed")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_convert(config.HOUSTON_TIMEZONE).dt.tz_localize(None)
    return parsed.dt.floor("h")


def load_traffic_archive(frame: pd.DataFrame, h3_resolution: int = 9) -> pd.DataFrame:
    """Normalise a historical traffic table to columns ``cell, hour, ratio, bucket``.

    Accepted columns (case-insensitive):
      time  : ``hour`` or ``timestamp`` (ISO; a UTC offset is converted to Houston local time)
      place : ``cell_id`` (H3) or ``lat`` + ``lng``
      level : ``congestion_ratio`` (0..1) or ``current_speed`` + ``free_flow_speed`` (any one unit)
    Several segments in the same cell-hour are averaged.
    """
    cols = {c.lower().strip(): c for c in frame.columns}
    pick = lambda *names: next((cols[n] for n in names if n in cols), None)  # noqa: E731
    time_col = pick("hour", "timestamp", "time")
    if time_col is None:
        raise ValueError("traffic archive needs an 'hour' or 'timestamp' column")
    out = pd.DataFrame({"hour": _local_hours(frame[time_col])})

    cell_col, lat_col, lng_col = pick("cell_id", "cell", "h3"), pick("lat", "latitude"), pick("lng", "lon", "longitude")
    if cell_col is not None:
        out["cell"] = frame[cell_col].astype(str)
    elif lat_col is not None and lng_col is not None:
        import h3
        out["cell"] = [h3.latlng_to_cell(float(a), float(b), h3_resolution) if pd.notna(a) and pd.notna(b) else None
                       for a, b in zip(frame[lat_col], frame[lng_col])]
    else:
        raise ValueError("traffic archive needs 'cell_id' or 'lat' and 'lng' columns")

    ratio_col = pick("congestion_ratio", "ratio")
    speed_col, free_col = pick("current_speed", "speed"), pick("free_flow_speed", "freeflow_speed", "free_flow")
    if ratio_col is not None:
        ratio = pd.to_numeric(frame[ratio_col], errors="coerce")
    elif speed_col is not None and free_col is not None:
        speed = pd.to_numeric(frame[speed_col], errors="coerce")
        free = pd.to_numeric(frame[free_col], errors="coerce")
        ratio = 1.0 - speed / free.where(free > 0)
    else:
        raise ValueError("traffic archive needs 'congestion_ratio' or 'current_speed' and 'free_flow_speed'")
    out["ratio"] = ratio.clip(lower=0.0, upper=1.0)
    out = out.dropna(subset=["hour", "cell", "ratio"])
    if out.empty:
        raise ValueError("traffic archive has no usable rows")
    out = out.groupby(["cell", "hour"], as_index=False)["ratio"].mean()
    out["bucket"] = ratio_bucket(out["ratio"].to_numpy())
    return out


def crash_hours(crashes: pd.DataFrame) -> pd.DataFrame:
    """Crash rows (``cell``/``cell_id`` and ``Start_Time``/``start_time``) -> ``cell, hour``."""
    cols = {c.lower(): c for c in crashes.columns}
    cell_col = cols.get("cell") or cols.get("cell_id")
    time_col = cols.get("start_time")
    if cell_col is None or time_col is None:
        raise ValueError("crash table needs a cell (or cell_id) and a Start_Time column")
    out = pd.DataFrame({"cell": crashes[cell_col].astype(str), "hour": _local_hours(crashes[time_col])})
    return out.dropna()


# ------------------------------------------------------------------ fitting
def _params() -> dict[str, Any]:
    return {
        "buckets": list(BUCKETS),
        "free_max": config.TRAFFIC_FREE_MAX,
        "moderate_max": config.TRAFFIC_MODERATE_MAX,
        "prior_strength": config.TRAFFIC_LIFT_PRIOR_STRENGTH,
        "min_crashes": config.TRAFFIC_LIFT_MIN_CRASHES,
        "min_hours": config.TRAFFIC_LIFT_MIN_HOURS,
        "strength": config.TRAFFIC_LIFT_STRENGTH,
        "floor": config.TRAFFIC_LIFT_FLOOR,
        "cap": config.TRAFFIC_LIFT_CAP,
        "timezone": config.HOUSTON_TIMEZONE,
    }


def _bounded(rr: float) -> float:
    if not math.isfinite(rr):
        return 1.0
    return float(min(config.TRAFFIC_LIFT_CAP, max(config.TRAFFIC_LIFT_FLOOR, 1.0 + config.TRAFFIC_LIFT_STRENGTH * (rr - 1.0))))


def _counts(crashes: pd.DataFrame, archive: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per cell x bucket: archive hours and matched crashes."""
    hours = archive.groupby(["cell", "bucket"]).size().unstack(fill_value=0).reindex(columns=BUCKETS, fill_value=0)
    matched = crashes.merge(archive[["cell", "hour", "bucket"]], on=["cell", "hour"], how="inner")
    k = matched.groupby(["cell", "bucket"]).size().unstack(fill_value=0).reindex(columns=BUCKETS, fill_value=0)
    k = k.reindex(hours.index, fill_value=0)
    return hours.astype(float), k.astype(float)


def _citywide(hours: pd.DataFrame, k: pd.DataFrame) -> dict[str, dict[str, float]]:
    total_h, total_k = float(hours.to_numpy().sum()), float(k.to_numpy().sum())
    city: dict[str, dict[str, float]] = {}
    for b in BUCKETS:
        h_share = float(hours[b].sum()) / total_h if total_h else 0.0
        k_share = float(k[b].sum()) / total_k if total_k else 0.0
        rr = k_share / h_share if h_share > 0 else 1.0
        city[b] = {"hour_share": h_share, "crash_share": k_share, "rr": rr, "multiplier": _bounded(rr)}
    return city


def _cell_rates(hours: pd.DataFrame, k: pd.DataFrame, city: dict[str, dict[str, float]]) -> pd.DataFrame:
    """Shrunk per-cell rate ratios, one column per bucket (NaN where the cell has no hours in a bucket)."""
    h_tot = hours.sum(axis=1).replace(0, np.nan)
    n = k.sum(axis=1)
    exposure = hours.div(h_tot, axis=0)
    rr_city = np.array([city[b]["rr"] for b in BUCKETS])
    expected = exposure.mul(rr_city, axis=1)
    expected = expected.div(expected.sum(axis=1).replace(0, np.nan), axis=0)
    prior = config.TRAFFIC_LIFT_PRIOR_STRENGTH
    share = (k + prior * expected).div(n + prior, axis=0)
    return share.div(exposure.where(exposure > 0))


def fit_traffic_lift(crashes: pd.DataFrame, archive: pd.DataFrame) -> dict[str, Any]:
    """Learn citywide and per-cell bucket multipliers from matched history."""
    hours, k = _counts(crashes, archive)
    if hours.empty:
        raise ValueError("no cell-hours in the traffic archive")
    city = _citywide(hours, k)
    rates = _cell_rates(hours, k, city)
    n = k.sum(axis=1)
    cells: dict[str, Any] = {}
    for cell_id in hours.index:
        if n[cell_id] < config.TRAFFIC_LIFT_MIN_CRASHES:
            continue
        buckets: dict[str, Any] = {}
        for b in BUCKETS:
            h = float(hours.at[cell_id, b])
            rr = float(rates.at[cell_id, b]) if h >= config.TRAFFIC_LIFT_MIN_HOURS else float("nan")
            if not math.isfinite(rr):
                continue
            buckets[b] = {"hours": int(h), "crashes": int(k.at[cell_id, b]), "rr": rr, "multiplier": _bounded(rr)}
        if buckets:
            cells[str(cell_id)] = {"n": int(n[cell_id]), "buckets": buckets}
    return {"citywide": city, "cells": cells,
            "matched_crashes": int(k.to_numpy().sum()), "cell_hours": int(hours.to_numpy().sum()),
            "cells_covered": int(len(hours))}


def multiplier_table(fitted: dict[str, Any]) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    city = {b: float(fitted["citywide"][b]["multiplier"]) for b in BUCKETS}
    cell = {(cid, b): float(row["multiplier"]) for cid, entry in fitted["cells"].items()
            for b, row in entry["buckets"].items()}
    return city, cell


# ------------------------------------------------------------------ validation
def _loglik_gain(crashes: pd.DataFrame, archive: pd.DataFrame, fitted: dict[str, Any],
                 bootstrap: int, seed: int) -> dict[str, Any]:
    hours, k = _counts(crashes, archive)
    n = k.sum(axis=1)
    keep = n > 0
    hours, k, n = hours[keep], k[keep], n[keep]
    if hours.empty:
        return {"holdout_crashes": 0, "holdout_cells": 0, "gain_per_crash": None, "ci": [None, None]}
    city, cell = multiplier_table(fitted)
    m = np.array([[cell.get((cid, b), city[b]) for b in BUCKETS] for cid in hours.index])
    H, K, N = hours.to_numpy(), k.to_numpy(), n.to_numpy()

    def per_cell_ll(mult: np.ndarray) -> np.ndarray:
        weight = H * mult
        lam = weight / weight.sum(axis=1, keepdims=True) * N[:, None]   # each cell keeps its own total
        with np.errstate(divide="ignore", invalid="ignore"):
            terms = np.where(K > 0, K * np.log(np.where(lam > 0, lam, np.nan)), 0.0) - lam
        return np.nansum(terms, axis=1)

    gain = per_cell_ll(m) - per_cell_ll(np.ones_like(m))
    total = float(N.sum())
    point = float(gain.sum() / total)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(bootstrap):
        idx = rng.integers(0, len(gain), len(gain))
        draws.append(float(gain[idx].sum() / N[idx].sum()))
    lo, hi = (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))) if draws else (None, None)
    return {"holdout_crashes": int(total), "holdout_cells": int(len(gain)),
            "gain_per_crash": point, "ci": [lo, hi]}


def validate_traffic_lift(crashes: pd.DataFrame, archive: pd.DataFrame, holdout_year: int,
                          bootstrap: int = 300, seed: int = 7) -> dict[str, Any]:
    """Fit on hours before ``holdout_year``; score on that year. Verdict PASS / FAIL / INSUFFICIENT_DATA."""
    start = pd.Timestamp(f"{holdout_year}-01-01")
    end = pd.Timestamp(f"{holdout_year + 1}-01-01")
    train_c, test_c = crashes[crashes.hour < start], crashes[(crashes.hour >= start) & (crashes.hour < end)]
    train_a, test_a = archive[archive.hour < start], archive[(archive.hour >= start) & (archive.hour < end)]
    if train_a.empty or test_a.empty:
        return {"verdict": "INSUFFICIENT_DATA", "holdout_year": holdout_year,
                "reason": "the archive needs hours both before and inside the holdout year"}
    fitted = fit_traffic_lift(train_c, train_a)
    score = _loglik_gain(test_c, test_a, fitted, bootstrap, seed)
    holdout_city = _citywide(*_counts(test_c, test_a))
    same_direction = all(
        (fitted["citywide"][b]["rr"] - 1.0) * (holdout_city[b]["rr"] - 1.0) >= 0
        for b in ("free", "heavy") if fitted["citywide"][b]["hour_share"] > 0 and holdout_city[b]["hour_share"] > 0)
    if score["holdout_crashes"] < config.TRAFFIC_LIFT_MIN_HOLDOUT_CRASHES or score["ci"][0] is None:
        verdict, reason = "INSUFFICIENT_DATA", (
            f"{score['holdout_crashes']} held-out crashes fall in archived cell-hours; "
            f"at least {config.TRAFFIC_LIFT_MIN_HOLDOUT_CRASHES} are needed")
    elif score["ci"][0] > 0 and same_direction:
        verdict, reason = "PASS", "congestion multipliers predict held-out crash timing better than a flat rate"
    else:
        verdict, reason = "FAIL", ("no reliable improvement on the held-out year" if score["ci"][0] <= 0
                                   else "the citywide direction of the effect flipped in the held-out year")
    return {"verdict": verdict, "reason": reason, "holdout_year": holdout_year,
            "train_matched_crashes": fitted["matched_crashes"], **score,
            "citywide_train_rr": {b: fitted["citywide"][b]["rr"] for b in BUCKETS},
            "citywide_holdout_rr": {b: holdout_city[b]["rr"] for b in BUCKETS},
            "direction_consistent": same_direction}


def build_document(crashes: pd.DataFrame, archive: pd.DataFrame, holdout_year: int | None,
                   source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate on ``holdout_year`` (the latest archived year by default), then fit on everything."""
    crash_rows = crash_hours(crashes) if "hour" not in crashes.columns else crashes
    if holdout_year is None:
        holdout_year = int(archive.hour.max().year)
    validation = validate_traffic_lift(crash_rows, archive, holdout_year)
    fitted = fit_traffic_lift(crash_rows, archive)
    return {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "parameters": _params(),
        "source": {
            "archive_cell_hours": fitted["cell_hours"], "archive_cells": fitted["cells_covered"],
            "archive_start": archive.hour.min().isoformat(), "archive_end": archive.hour.max().isoformat(),
            "matched_crashes": fitted["matched_crashes"], **(source or {}),
        },
        "validation": validation,
        "citywide": fitted["citywide"],
        "cells": fitted["cells"],
    }


def write_document(document: dict[str, Any], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.write_bytes(raw)
    return "traffic-" + hashlib.sha256(raw).hexdigest()[:8]


# ------------------------------------------------------------------ runtime
BUCKET_WORDS = {"free": "free-flowing", "moderate": "moderately congested", "heavy": "heavily congested"}


class TrafficLift:
    """Runtime view of ``traffic_lift.json``: bucket -> multiplier for a cell, plus an explanation."""

    def __init__(self, document: dict[str, Any], version: str):
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("traffic-lift artifact has an unsupported schema")
        citywide = document.get("citywide") or {}
        self.citywide = {b: float(citywide[b]["multiplier"]) for b in BUCKETS if b in citywide}
        if set(self.citywide) != set(BUCKETS) or not all(math.isfinite(v) and v > 0 for v in self.citywide.values()):
            raise ValueError("traffic-lift artifact has invalid citywide multipliers")
        self.cells: dict[str, dict[str, Any]] = document.get("cells") or {}
        self.validation: dict[str, Any] = document.get("validation") or {}
        self.version = version
        self.document = document

    @classmethod
    def load(cls, path: Path) -> "TrafficLift | None":
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
            return cls(json.loads(raw), "traffic-" + hashlib.sha256(raw).hexdigest()[:8])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Traffic lift disabled: artifact could not be read (%s)", exc)
            return None

    @property
    def verdict(self) -> str:
        return str(self.validation.get("verdict") or "UNVALIDATED")

    @property
    def applied(self) -> bool:
        """Scores move only when the held-out check passed and the operator has not switched it off."""
        return self.verdict == "PASS" and config.TRAFFIC_LIFT_ENABLED

    def multiplier(self, cell_id: str, bucket: str) -> tuple[float, str]:
        """(multiplier, basis) where basis is 'cell' or 'citywide'."""
        row = (self.cells.get(cell_id) or {}).get("buckets", {}).get(bucket)
        if row is not None:
            return float(row["multiplier"]), "cell"
        return self.citywide.get(bucket, 1.0), "citywide"

    def sensitive_cells(self, threshold: float = 0.1) -> set[str]:
        return {cid for cid, entry in self.cells.items()
                if any(abs(float(row.get("multiplier", 1.0)) - 1.0) >= threshold
                       for row in entry.get("buckets", {}).values())}

    def adjustment(self, cell_id: str, reading: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Multiplier actually applied (1.0 when not applied) and the explanation shown to the user."""
        bucket = reading["bucket"]
        m, basis = self.multiplier(cell_id, bucket)
        word = BUCKET_WORDS.get(bucket, bucket)
        if basis == "cell":
            row = self.cells[cell_id]["buckets"][bucket]
            evidence = (f"Traffic here is {word} now. In this cell's history, {row['crashes']} of "
                        f"{self.cells[cell_id]['n']} matched crashes happened in {row['hours']} {bucket}-traffic hours, "
                        f"about {row['rr']:.2f}x its average hourly crash rate.")
        else:
            rr = float(self.document["citywide"][bucket]["rr"])
            evidence = (f"Traffic here is {word} now. Citywide, {bucket}-traffic hours have had about {rr:.2f}x the "
                        f"average hourly crash rate (this cell has too little matched history of its own).")
        direction = "raises" if m > 1.0 else "lowers" if m < 1.0 else "leaves"
        if self.applied:
            evidence += f" This {direction} the live likelihood (x{m:.2f})."
        else:
            evidence += (f" Not applied: the learned effect has not passed its held-out check "
                         f"({self.verdict.replace('_', ' ').lower()}), so the score is unchanged.")
        return (m if self.applied else 1.0), {
            "kind": "traffic_lift" if self.applied else "traffic_context",
            **({"multiplier": m} if self.applied else {}),
            "reason": evidence,
            "source": "Live traffic (TomTom) with learned congestion effect",
            "observed_at": reading.get("observed_at"),
        }
