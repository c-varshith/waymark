#!/usr/bin/env python3
"""Held-out 2022 evaluation and impact report for per-cell condition multipliers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blackspot_pipeline as pipeline
from backend import realtime_config as config
from backend.config import settings
from backend.ml_runtime import InferenceRuntime
from scripts.build_condition_lift import CONDITIONS, build_lift, prepare_training_data


def top_fraction_precision(labels: np.ndarray, scores: np.ndarray, fraction: float = 0.05) -> float:
    count = max(1, int(np.ceil(len(labels) * fraction)))
    selected = np.argpartition(scores, -count)[-count:]
    return float(labels[selected].mean()) if len(labels) else float("nan")


def binary_metrics(labels: np.ndarray, scores: np.ndarray) -> dict[str, float | None]:
    if len(labels) == 0 or len(np.unique(labels)) < 2:
        auc = None
    else:
        auc = float(roc_auc_score(labels, scores))
    return {"auc": auc, "precision_top_5_percent": top_fraction_precision(labels, scores)}


def paired_bootstrap_delta(labels: np.ndarray, baseline: np.ndarray, lifted: np.ndarray,
                           replicates: int = 1000, seed: int = 20261114) -> dict:
    """Percentile intervals for lifted-minus-baseline metrics on paired cell resamples."""
    if replicates < 100:
        raise ValueError("paired bootstrap requires at least 100 replicates")
    labels = np.asarray(labels, dtype=np.int8)
    baseline = np.asarray(baseline, dtype=float)
    lifted = np.asarray(lifted, dtype=float)
    if not (len(labels) == len(baseline) == len(lifted)):
        raise ValueError("labels and score vectors must have the same cell count")
    rng = np.random.default_rng(seed)
    auc_deltas = np.empty(replicates, dtype=float)
    precision_deltas = np.empty(replicates, dtype=float)
    size = len(labels)
    for iteration in range(replicates):
        indices = rng.integers(0, size, size=size)
        sample_labels = labels[indices]
        if len(np.unique(sample_labels)) < 2:
            auc_deltas[iteration] = np.nan
        else:
            auc_deltas[iteration] = (
                roc_auc_score(sample_labels, lifted[indices])
                - roc_auc_score(sample_labels, baseline[indices])
            )
        precision_deltas[iteration] = (
            top_fraction_precision(sample_labels, lifted[indices])
            - top_fraction_precision(sample_labels, baseline[indices])
        )
    valid_auc = auc_deltas[np.isfinite(auc_deltas)]
    if not len(valid_auc):
        auc_interval = [None, None]
    else:
        auc_interval = [float(value) for value in np.quantile(valid_auc, [0.025, 0.975])]
    precision_interval = [float(value) for value in np.quantile(precision_deltas, [0.025, 0.975])]
    return {
        "method": "paired cell bootstrap; same sampled cell indices for baseline and lifted",
        "replicates": replicates,
        "seed": seed,
        "lifted_minus_baseline_auc_ci95": auc_interval,
        "lifted_minus_baseline_precision_top_5_percent_ci95": precision_interval,
    }


def rho_by_cell(history: pd.DataFrame, citywide: dict[str, dict], condition: str) -> dict[str, float]:
    p = float(citywide[condition]["p"])
    if p <= 0:
        return {}
    prior = float(config.LIFT_PRIOR_STRENGTH)
    values = {}
    for cell_id, group in history.groupby("cell", sort=False):
        n = len(group)
        k = int(group[condition].sum())
        values[str(cell_id)] = ((k + prior * p) / (n + prior)) / p
    return values


def validate_threshold(crashes: pd.DataFrame, weather: pd.DataFrame, runtime: InferenceRuntime,
                       through_year: int, rain_mm: float, bootstrap_replicates: int = 1000,
                       bootstrap_seed: int = 20261114) -> dict:
    lift = build_lift(crashes, weather, through_year, rain_mm)
    train, _ = prepare_training_data(crashes, weather, through_year, rain_mm)
    tagged_all, heldout_weather = prepare_training_data(crashes, weather, 2022, rain_mm)
    holdout = tagged_all.loc[tagged_all.year == 2022].copy()
    heldout_weather = heldout_weather.loc[heldout_weather.time.dt.year == 2022]
    if holdout.empty:
        raise ValueError("no tagged 2022 crashes found for held-out evaluation")

    rho_tables = {condition: rho_by_cell(train, lift["citywide"], condition) for condition in CONDITIONS}
    holdout_counts = holdout.groupby("cell").size().to_dict()
    holdout_condition_counts = {
        condition: holdout.loc[holdout[condition]].groupby("cell").size().to_dict()
        for condition in CONDITIONS
    }
    correlation = {}
    for condition in CONDITIONS:
        eligible = [cell for cell, n in holdout_counts.items() if n >= 5 and str(cell) in rho_tables[condition]]
        historical_rho = [rho_tables[condition][str(cell)] for cell in eligible]
        future_share = [holdout_condition_counts[condition].get(cell, 0) / holdout_counts[cell]
                        for cell in eligible]
        if len(eligible) < 2:
            rho, pvalue = float("nan"), float("nan")
        else:
            result = spearmanr(historical_rho, future_share)
            rho, pvalue = float(result.statistic), float(result.pvalue)
            if not np.isfinite(rho):
                rho = None
            if not np.isfinite(pvalue):
                pvalue = None
        correlation[condition] = {"cells_with_at_least_5_holdout_crashes": len(eligible),
                                  "spearman_rho": rho, "p_value": pvalue}

    cell_ids = list(runtime.features.index)
    model_probabilities = np.column_stack([
        model.predict_proba(runtime.features[runtime.feature_names])[:, 1] for model in runtime.models
    ]).mean(axis=1)
    baseline_scores = runtime._rank_scores(model_probabilities, runtime.probability_reference)
    baseline_by_id = dict(zip(cell_ids, model_probabilities))
    lift_cells = lift["cells"]
    condition_results = {}
    impact = {}
    per_condition_vectors = {}
    for condition in CONDITIONS:
        multiplier = np.ones(len(cell_ids), dtype=float)
        prone_count = 0
        for i, cell_id in enumerate(cell_ids):
            value = lift_cells.get(cell_id, {}).get("conditions", {}).get(condition, {})
            if value.get("prone"):
                multiplier[i] = float(value["multiplier"])
                prone_count += 1
        per_condition_vectors[condition] = multiplier
        labels = np.asarray([holdout_condition_counts[condition].get(cid, 0) > 0 for cid in cell_ids], dtype=int)
        base = binary_metrics(labels, model_probabilities)
        lifted_probabilities = np.minimum(1.0, model_probabilities * multiplier)
        lifted = binary_metrics(labels, lifted_probabilities)
        condition_results[condition] = {"label_positive_cells": int(labels.sum()),
                                        "baseline": base, "lifted": lifted,
                                        "paired_bootstrap_delta": paired_bootstrap_delta(
                                            labels, model_probabilities, lifted_probabilities,
                                            bootstrap_replicates,
                                            bootstrap_seed + int(round(rain_mm * 1000)) + CONDITIONS.index(condition) * 10000)}
        score_after = runtime._rank_scores(lifted_probabilities, runtime.probability_reference)
        shifts = score_after - baseline_scores
        impact[condition] = {
            "prone_cells": prone_count,
            "mean_score_shift": float(np.mean(shifts)),
            "cells_crossing_alert_threshold_up": int(np.count_nonzero(
                (baseline_scores < config.ALERT_THRESHOLD) & (score_after >= config.ALERT_THRESHOLD))),
            "largest_single_cell_rise": float(np.max(shifts)) if len(shifts) else 0.0,
        }

    combined_multiplier = np.ones(len(cell_ids), dtype=float)
    for condition in CONDITIONS:
        combined_multiplier *= per_condition_vectors[condition]
    combined_multiplier = np.minimum(combined_multiplier, config.LIFT_CAP_TOTAL)
    combined_probabilities = np.minimum(1.0, model_probabilities * combined_multiplier)
    combined_scores = runtime._rank_scores(combined_probabilities, runtime.probability_reference)
    combined_shift = combined_scores - baseline_scores
    impact["all_three"] = {
        "prone_cells": int(np.count_nonzero(combined_multiplier > 1.0)),
        "mean_score_shift": float(np.mean(combined_shift)),
        "cells_crossing_alert_threshold_up": int(np.count_nonzero(
            (baseline_scores < config.ALERT_THRESHOLD) & (combined_scores >= config.ALERT_THRESHOLD))),
        "largest_single_cell_rise": float(np.max(combined_shift)) if len(combined_shift) else 0.0,
    }
    impact["alert_threshold"] = config.ALERT_THRESHOLD
    impact["fraction_of_city_crossing_by_condition"] = {
        condition: impact[condition]["cells_crossing_alert_threshold_up"] / len(cell_ids)
        for condition in CONDITIONS
    }
    return {
        "rain_mm": rain_mm,
        "training_through_year": through_year,
        "target_year": 2022,
        "training_crashes": lift["source"]["crash_rows"],
        "holdout_crashes": int(len(holdout)),
        "holdout_weather_hours": int(len(heldout_weather)),
        "holdout_condition_samples": {
            condition: {
                "weather_hours": int(heldout_weather[condition].sum()),
                "crash_rows": int(holdout[condition].sum()),
                "positive_cells": int(holdout.loc[holdout[condition], "cell"].nunique()),
            }
            for condition in CONDITIONS
        },
        "prone_cells_by_condition": {
            condition: int(sum(item["conditions"][condition]["prone"] for item in lift_cells.values()))
            for condition in CONDITIONS
        },
        "rho_correlation": correlation,
        "classification": condition_results,
        "impact": impact,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="US_Accidents_March23.csv")
    parser.add_argument("--weather", default="data/external/weather_houston_hourly.csv")
    parser.add_argument("--through-year", type=int, default=2021)
    parser.add_argument("--rain-mm", type=float, action="append", default=None,
                        help="rain threshold; repeat for a robustness sweep (default: configured value)")
    parser.add_argument("--bootstrap-replicates", type=int, default=1000,
                        help="paired cell-bootstrap replicates for AUC and top-5%% precision deltas")
    parser.add_argument("--bootstrap-seed", type=int, default=20261114)
    parser.add_argument("--output", help="optional JSON report file")
    args = parser.parse_args()
    crashes = pipeline.load(SimpleNamespace(csv=args.csv, state="TX", city="Houston", res=9))
    weather = pd.read_csv(args.weather, parse_dates=["time"])
    runtime = InferenceRuntime.load(settings.ml_artifact_dir, settings.ml_feature_store)
    runtime.warm_score_cache()
    thresholds = args.rain_mm or [config.RAIN_MM]
    results = [validate_threshold(crashes, weather, runtime, args.through_year, threshold,
                                  args.bootstrap_replicates, args.bootstrap_seed)
               for threshold in thresholds]

    configured = next((r for r in results if np.isclose(r["rain_mm"], config.RAIN_MM)), None)
    robust_rain = [r for r in results if r["rain_mm"] in (0.5, 1.0, 2.0)]
    robustness_pass = len(robust_rain) == 3 and all(
        r["rho_correlation"]["rain"]["spearman_rho"] > 0.1
        and r["classification"]["rain"]["lifted"]["auc"] is not None
        and r["classification"]["rain"]["baseline"]["auc"] is not None
        and r["classification"]["rain"]["lifted"]["auc"] > r["classification"]["rain"]["baseline"]["auc"]
        for r in robust_rain
    )
    default_improves_rain_and_night = configured is not None and all(
        configured["classification"][condition]["lifted"]["auc"] is not None
        and configured["classification"][condition]["baseline"]["auc"] is not None
        and configured["classification"][condition]["lifted"]["auc"] > configured["classification"][condition]["baseline"]["auc"]
        for condition in ("rain", "night")
    )
    verdict = "DEFAULT_ON" if robustness_pass and default_improves_rain_and_night else "OPT_IN"
    report = {"condition_lift_verdict": verdict, "rain_robustness_pass": robustness_pass,
              "configured_threshold_mm": config.RAIN_MM, "threshold_results": results}
    rendered = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
