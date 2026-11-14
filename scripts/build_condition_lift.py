#!/usr/bin/env python3
"""Build the offline, per-cell historical condition-lift table from the weather archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blackspot_pipeline as pipeline
from backend import realtime_config as config
from backend.conditions import historical_flags

CONDITIONS = ("rain", "night", "rush")


def build_lift(crashes: pd.DataFrame, weather: pd.DataFrame, through_year: int,
               rain_mm: float | None = None) -> dict:
    """Return the table document; input crashes must be loaded by blackspot_pipeline.load."""
    threshold = config.RAIN_MM if rain_mm is None else float(rain_mm)
    joined, archive = prepare_training_data(crashes, weather, through_year, threshold)

    p: dict[str, float] = {}
    h: dict[str, float] = {}
    citywide: dict[str, dict[str, float]] = {}
    for condition in CONDITIONS:
        p[condition] = float(joined[condition].mean())
        h[condition] = float(archive[condition].mean())
        citywide[condition] = {
            "p": p[condition], "h": h[condition],
            "L": p[condition] / h[condition] if h[condition] > 0 else 0.0,
        }

    details: dict[str, dict] = {}
    prior = float(config.LIFT_PRIOR_STRENGTH)
    for cell_id, group in joined.groupby("cell", sort=True):
        n = int(len(group))
        condition_rows: dict[str, dict] = {}
        any_prone = False
        for condition in CONDITIONS:
            k = int(group[condition].sum())
            prevalence = p[condition]
            smoothed_share = ((k + prior * prevalence) / (n + prior)) if n + prior > 0 else prevalence
            rho = smoothed_share / prevalence if prevalence > 0 else 0.0
            prone = n >= config.LIFT_MIN_CRASHES and rho >= config.LIFT_MIN_RHO
            multiplier = (min(config.LIFT_CAP_PER_CONDITION,
                              1.0 + config.LIFT_STRENGTH[condition] * (rho - 1.0))
                          if prone else 1.0)
            condition_rows[condition] = {
                "k": k, "smoothed_share": smoothed_share, "rho": rho,
                "prone": prone, "multiplier": max(1.0, float(multiplier)),
            }
            any_prone |= prone
        if any_prone:
            details[str(cell_id)] = {"n": n, "conditions": condition_rows}

    crash_times = pd.to_datetime(joined.Start_Time)
    return {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "through_year": int(through_year),
        "parameters": {
            "rain_mm": threshold,
            "rain_exit_mm": config.RAIN_EXIT_MM,
            "rush_windows": [[int(start), int(end)] for start, end in config.RUSH_WINDOWS],
            "timezone": config.HOUSTON_TIMEZONE,
            "prior_strength": config.LIFT_PRIOR_STRENGTH,
            "min_crashes": config.LIFT_MIN_CRASHES,
            "min_rho": config.LIFT_MIN_RHO,
            "strength": config.LIFT_STRENGTH,
            "cap_per_condition": config.LIFT_CAP_PER_CONDITION,
            "cap_total": config.LIFT_CAP_TOTAL,
        },
        "source": {
            "crash_rows": int(len(joined)),
            "crash_start": crash_times.min().isoformat(),
            "crash_end": crash_times.max().isoformat(),
            "weather_hours": int(len(archive)),
            "weather_start": archive.time.min().isoformat(),
            "weather_end": archive.time.max().isoformat(),
            "weather_source": "Open-Meteo historical archive",
            "crash_source": "US Accidents Houston rows linked by local start hour",
        },
        "citywide": citywide,
        "cells": details,
    }


def prepare_training_data(crashes: pd.DataFrame, weather: pd.DataFrame, through_year: int,
                          threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join complete-year crash rows to archive hour tags; shared by builder and validator."""
    if not 2017 <= through_year <= 2022:
        raise ValueError("through_year must select complete Houston years 2017 through 2022")
    required_crashes = {"cell", "Start_Time", "year"}
    required_weather = {"time", "precipitation", "is_day"}
    if not required_crashes.issubset(crashes.columns) or not required_weather.issubset(weather.columns):
        raise ValueError("crash or archive table is missing required lift columns")

    history = crashes.loc[crashes.year.between(2017, through_year), ["cell", "Start_Time", "year"]].copy()
    if history.empty:
        raise ValueError("no crashes found in the requested complete-year training interval")
    history["hour"] = pd.to_datetime(history.Start_Time).dt.floor("h")
    archive = weather[["time", "precipitation", "is_day"]].copy()
    archive["time"] = pd.to_datetime(archive.time)
    start = pd.Timestamp(f"2017-01-01 00:00:00")
    end = pd.Timestamp(f"{through_year}-12-31 23:00:00")
    archive = archive.loc[archive.time.between(start, end)].copy()
    archive = archive.dropna(subset=["time", "precipitation", "is_day"])
    if archive.time.duplicated().any():
        raise ValueError("weather archive has duplicate hourly timestamps")
    expected_hours = int((end - start).total_seconds() // 3600) + 1
    if len(archive) != expected_hours or archive.time.min() != start or archive.time.max() != end:
        raise ValueError(f"weather archive must contain every complete-year hour ({expected_hours} expected)")
    archive["rain"] = archive.precipitation.astype(float) >= threshold
    archive["night"] = archive.is_day.astype(int) == 0
    archive["rush"] = [historical_flags(0.0, 1, t.to_pydatetime())["rush"] for t in archive.time]

    joined = history.merge(archive[["time", "rain", "night", "rush"]],
                           left_on="hour", right_on="time", how="left", validate="many_to_one")
    if joined[["rain", "night", "rush"]].isna().any().any():
        missing = int(joined.rain.isna().sum())
        raise ValueError(f"{missing} training crashes have no matching hourly weather record")
    joined[list(CONDITIONS)] = joined[list(CONDITIONS)].astype(bool)
    return joined, archive


def write_document(document: dict, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.write_bytes(raw)
    return "lift-" + hashlib.sha256(raw).hexdigest()[:8]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="US_Accidents_March23.csv")
    parser.add_argument("--weather", default="data/external/weather_houston_hourly.csv")
    parser.add_argument("--through-year", type=int, required=True)
    parser.add_argument("--rain-mm", type=float, default=None,
                        help="rain threshold override used for validation robustness runs")
    parser.add_argument("--out", default="backend/ml_artifacts/condition_lift.json")
    args = parser.parse_args()
    crashes = pipeline.load(SimpleNamespace(csv=args.csv, state="TX", city="Houston", res=9))
    weather = pd.read_csv(args.weather, parse_dates=["time"])
    document = build_lift(crashes, weather, args.through_year, args.rain_mm)
    version = write_document(document, Path(args.out))
    print(json.dumps({"condition_lift_version": version, "through_year": args.through_year,
                      "crash_rows": document["source"]["crash_rows"],
                      "prone_cells": len(document["cells"]),
                      "citywide": document["citywide"]}, indent=2))


if __name__ == "__main__":
    main()
