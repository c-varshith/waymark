#!/usr/bin/env python3
"""Repeatable local benchmark for WAYMARK's packaged inference runtime."""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import time
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.ml_runtime import InferenceRuntime
from backend.schemas import PredictionOverrides


def percentile(samples: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(samples, dtype=float), q))


def measure(label: str, fn, runs: int) -> dict[str, float | int | str]:
    fn()  # warm-up
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return {"case": label, "runs": runs, "p50_ms": round(statistics.median(samples), 3),
            "p95_ms": round(percentile(samples, 95), 3), "min_ms": round(min(samples), 3),
            "max_ms": round(max(samples), 3)}


def payload_sizes(value) -> dict[str, int]:
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    return {"raw_bytes": len(raw), "gzip_bytes": len(gzip.compress(raw))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=15, help="Measured runs per case (minimum 15).")
    parser.add_argument("--output", type=Path, help="Optional JSON output file.")
    args = parser.parse_args()
    if args.runs < 15:
        parser.error("--runs must be at least 15")

    runtime = InferenceRuntime.load(settings.ml_artifact_dir, settings.ml_feature_store)
    ids = runtime.features.index.tolist()
    if len(ids) < 7:
        raise SystemExit("Feature store needs at least 7 cells.")
    rain_night_low = PredictionOverrides(weather_condition="rain", lighting="night", low_visibility=True)

    def uncached(overrides):
        key = runtime._override_key(overrides)
        def run():
            with runtime._cache_lock:
                runtime._score_cache.pop(key, None)
            return runtime.predict_batch(ids, overrides)
        return run

    cases = [
        ("single_with_shap", lambda: runtime.predict(ids[0], include_shap=True)),
        ("single_without_shap", lambda: runtime.predict(ids[0], include_shap=False)),
        ("batch_7_no_override_uncached", uncached(None)),
        ("full_city_no_override_uncached", uncached(None)),
        ("full_city_rain_night_low_visibility_uncached", uncached(rain_night_low)),
    ]
    results = [measure(name, fn, args.runs) for name, fn in cases]
    results.extend([
        measure("batch_7_no_override_cached", lambda: runtime.predict_batch(ids[:7]), args.runs),
        measure("full_city_no_override_cached", lambda: runtime.predict_batch(ids), args.runs),
        measure("full_city_rain_night_low_visibility_cached",
                lambda: runtime.predict_batch(ids, rain_night_low), args.runs),
    ])

    # Isolate the old per-cell serialization path from model.predict_proba. This helps
    # show whether time is being spent in LightGBM or in Python/DataFrame row handling.
    frame = runtime.features
    started = time.perf_counter()
    member_probs = np.column_stack([model.predict_proba(frame[runtime.feature_names])[:, 1]
                                    for model in runtime.models])
    model_predict_ms = (time.perf_counter() - started) * 1000
    row_loop_samples = []
    for _ in range(args.runs):
        started = time.perf_counter()
        for i, cell_id in enumerate(ids):
            runtime._serialize_prediction(cell_id, frame.loc[[cell_id]], member_probs[i],
                                         float(member_probs[i].std()), False)
        row_loop_samples.append((time.perf_counter() - started) * 1000)
    results.append({"case": "model_predict_proba_full_city_once", "runs": 1,
                    "p50_ms": round(model_predict_ms, 3), "p95_ms": round(model_predict_ms, 3),
                    "min_ms": round(model_predict_ms, 3), "max_ms": round(model_predict_ms, 3)})
    results.append({"case": "legacy_per_cell_serialization_loop_full_city", "runs": args.runs,
                    "p50_ms": round(statistics.median(row_loop_samples), 3),
                    "p95_ms": round(percentile(row_loop_samples, 95), 3),
                    "min_ms": round(min(row_loop_samples), 3), "max_ms": round(max(row_loop_samples), 3)})

    sample_single = runtime.predict(ids[0])
    sample_single.update({"model_version": runtime.model_version, "scored_at": "2026-11-14T00:00:00Z",
                          "context_source": "baseline", "stale_after_seconds": 120})
    sample_batch = runtime.predict_batch(ids)
    batch_payload = {"count": len(sample_batch), "items": sample_batch,
                     "model_version": runtime.model_version, "schema_version": runtime.schema_version,
                     "scored_at": "2026-11-14T00:00:00Z", "context_source": "baseline",
                     "stale_after_seconds": 120}
    output = {"feature_rows": len(ids), "feature_count": len(runtime.feature_names),
              "model_version": runtime.model_version, "runs_per_case": args.runs, "latency": results,
              "payload": {"single_prediction_with_metadata": payload_sizes(sample_single),
                          "full_city_batch_envelope_with_metadata": payload_sizes(batch_payload)}}
    rendered = json.dumps(output, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
