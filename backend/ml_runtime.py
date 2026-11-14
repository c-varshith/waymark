"""In-memory inference runtime for the packaged WAYMARK model."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import h3
import joblib
import numpy as np
import pandas as pd
import shap

from .services import FEATURE_LABELS
from .incident_overlay import DEFAULT_CONFIG, aggregate_details, effective_points, overlay_params
from .realtime_config import SWEEP_PUBLISH_DELTA
from . import realtime_config as config
from .traffic_lift import TrafficLift

MAX_SHAP_CONTRIBUTORS = 6
ACTIVE_INCIDENT_STATUSES = frozenset({"pending", "confirmed"})
CONDITIONS = ("rain", "night", "rush")
log = logging.getLogger("waymark.ml")


class MLRuntimeError(RuntimeError):
    """The packaged model or its baseline feature store is invalid or unavailable."""


class InferenceRuntime:
    """Loaded model, explainer, feature vectors and process-local live incident state."""

    def __init__(self, bundle: dict[str, Any], schema: dict[str, Any], explainer: Any,
                 features: pd.DataFrame, disagreement_threshold: float,
                 condition_lift: dict[str, Any] | None = None,
                 condition_lift_version: str | None = None):
        self.models = bundle["models"]
        self.feature_names = list(schema["feature_columns"])
        self.probability_reference = np.asarray(bundle["probability_reference"], dtype=float)
        self.explainer = explainer
        self.features = features
        self.resolution = int(schema["h3_resolution"])
        self.disagreement_threshold = float(disagreement_threshold)
        self.schema_version = schema.get("schema_version")
        self.model_version: str | None = None
        self._cell_positions = {cell_id: i for i, cell_id in enumerate(features.index)}
        self._active_incidents: dict[int, tuple[str, str, str, datetime, str | None]] = {}
        self._last_published_overlay_points: dict[str, float] = {}
        self._lock = threading.RLock()
        self._score_cache: OrderedDict[
            tuple[Any, ...], tuple[np.ndarray, np.ndarray, list[str], np.ndarray]
        ] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._score_cache_limit = 8
        self.condition_lift_enabled = False
        self.condition_lift_version: str | None = None
        self.condition_citywide: dict[str, dict[str, float]] = {}
        self.condition_lift_cells: dict[str, dict[str, Any]] = {}
        self.traffic_lift: TrafficLift | None = None
        self._condition_multiplier_vectors = {name: np.ones(len(features), dtype=float) for name in CONDITIONS}
        if condition_lift is not None:
            self._load_condition_lift(condition_lift, condition_lift_version)

    @staticmethod
    def overlay_params() -> dict[str, Any]:
        return overlay_params(DEFAULT_CONFIG)

    @classmethod
    def load(cls, artifact_dir: Path | str, feature_store_path: Path | str) -> "InferenceRuntime":
        artifact_dir = Path(artifact_dir)
        feature_store_path = Path(feature_store_path)
        model_path = artifact_dir / "model.pkl"
        explainer_path = artifact_dir / "explainer.pkl"
        schema_path = artifact_dir / "feature_schema.json"
        missing = [str(p) for p in (model_path, explainer_path, schema_path, feature_store_path) if not p.is_file()]
        if missing:
            raise MLRuntimeError("Missing inference files: " + ", ".join(missing))

        try:
            bundle = joblib.load(model_path)
            explainer = joblib.load(explainer_path)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            frame = pd.read_csv(feature_store_path, index_col=0)
        except Exception as exc:
            raise MLRuntimeError(f"Could not load the inference package: {exc}") from exc

        if not isinstance(bundle, dict) or not isinstance(schema, dict):
            raise MLRuntimeError("Model bundle and feature schema must be JSON/object mappings.")
        names = schema.get("feature_columns")
        models = bundle.get("models")
        if not isinstance(names, list) or not names or bundle.get("features") != names:
            raise MLRuntimeError("Model feature names do not match feature_schema.json.")
        if len(models or []) != 3:
            raise MLRuntimeError("Expected the packaged three-model LightGBM ensemble.")
        if int(schema.get("feature_count", -1)) != len(names):
            raise MLRuntimeError("Feature count does not match the schema.")
        for model in models:
            try:
                model_names = list(model.booster_.feature_name())
            except Exception as exc:
                raise MLRuntimeError("A packaged LightGBM model is not fitted.") from exc
            if model_names != names:
                raise MLRuntimeError("LightGBM feature order does not match feature_schema.json.")
            # Bound native OpenMP usage in the API process. The runtime is already
            # vectorized for batch requests, so one prediction thread avoids oversubscription.
            model.set_params(n_jobs=1)

        frame.index = frame.index.astype(str)
        if frame.index.has_duplicates:
            raise MLRuntimeError("Feature store contains duplicate H3 cell IDs.")
        if list(frame.columns) != names:
            raise MLRuntimeError("Feature-store columns do not match feature_schema.json.")
        try:
            frame = frame.astype(float)
        except (TypeError, ValueError) as exc:
            raise MLRuntimeError("Feature store contains non-numeric model inputs.") from exc
        matrix = frame.to_numpy(dtype=float)
        if not np.isfinite(matrix).all():
            raise MLRuntimeError("Feature store contains missing or non-finite values.")
        if not len(frame) or any(h3.get_resolution(cid) != int(schema["h3_resolution"]) for cid in frame.index):
            raise MLRuntimeError("Feature store is empty or contains cells at the wrong H3 resolution.")

        reference = np.asarray(bundle.get("probability_reference", []), dtype=float)
        if reference.ndim != 1 or not reference.size or not np.isfinite(reference).all():
            raise MLRuntimeError("The model package has no valid probability reference distribution.")
        if np.any((reference < 0) | (reference > 1)):
            raise MLRuntimeError("The probability reference contains values outside [0, 1].")

        try:
            member_probs = np.column_stack([m.predict_proba(frame[names])[:, 1] for m in models])
        except Exception as exc:
            raise MLRuntimeError(f"Model cannot score the feature store: {exc}") from exc
        disagreement_threshold = float(np.quantile(member_probs.std(axis=1), 0.75))
        lift_path = artifact_dir / "condition_lift.json"
        lift_document = None
        lift_version = None
        if lift_path.is_file():
            try:
                lift_bytes = lift_path.read_bytes()
                lift_document = json.loads(lift_bytes)
                lift_version = "lift-" + hashlib.sha256(lift_bytes).hexdigest()[:8]
            except (OSError, ValueError) as exc:
                log.warning("Condition lift is disabled because its artifact could not be read: %s", exc)
        runtime = cls(bundle, schema, explainer, frame, disagreement_threshold,
                      lift_document, lift_version)
        runtime.model_version = "lgbm3-" + hashlib.sha256(model_path.read_bytes()).hexdigest()[:8]
        runtime.traffic_lift = TrafficLift.load(artifact_dir / "traffic_lift.json")
        return runtime

    def _load_condition_lift(self, document: dict[str, Any], version: str | None) -> None:
        if document.get("schema_version") != 1 or not isinstance(document.get("cells"), dict):
            raise MLRuntimeError("Condition-lift artifact has an unsupported schema.")
        citywide = document.get("citywide")
        if not isinstance(citywide, dict) or any(name not in citywide for name in CONDITIONS):
            raise MLRuntimeError("Condition-lift artifact is missing citywide condition shares.")
        vectors = {name: np.ones(len(self.features), dtype=float) for name in CONDITIONS}
        cells = document["cells"]
        for cell_id, entry in cells.items():
            position = self._cell_positions.get(str(cell_id))
            if position is None or not isinstance(entry, dict):
                continue
            conditions = entry.get("conditions", {})
            if not isinstance(conditions, dict):
                continue
            for name in CONDITIONS:
                condition = conditions.get(name, {})
                if not isinstance(condition, dict) or not condition.get("prone"):
                    continue
                multiplier = float(condition.get("multiplier", 1.0))
                if not np.isfinite(multiplier) or multiplier < 1.0:
                    raise MLRuntimeError("Condition-lift multiplier must be finite and at least one.")
                vectors[name][position] = min(config.LIFT_CAP_PER_CONDITION, multiplier)
        self.condition_lift_cells = cells
        self.condition_citywide = citywide
        self._condition_multiplier_vectors = vectors
        self.condition_lift_version = version
        self.condition_lift_enabled = True

    def resolve_cell(self, cell_id: str | None = None, lat: float | None = None,
                     lng: float | None = None) -> str:
        if cell_id is not None:
            resolved = cell_id.strip().lower()
            if not h3.is_valid_cell(resolved) or h3.get_resolution(resolved) != self.resolution:
                raise ValueError("cell_id must be a valid H3 cell at the scored-cell resolution.")
        elif lat is not None and lng is not None:
            resolved = h3.latlng_to_cell(lat, lng, self.resolution)
        else:
            raise ValueError("Provide a cell_id or both lat and lng.")
        if resolved not in self.features.index:
            raise KeyError(resolved)
        return resolved

    def _apply_overrides(self, frame: pd.DataFrame, overrides: Any | None) -> pd.DataFrame:
        result = frame.copy()
        if overrides is None:
            return result
        weather = getattr(overrides, "weather_condition", None)
        lighting = getattr(overrides, "lighting", None)
        low_visibility = getattr(overrides, "low_visibility", None)
        if weather is not None:
            result.loc[:, "share_rain"] = 1.0 if weather == "rain" else 0.0
        if lighting is not None:
            result.loc[:, "share_night"] = 1.0 if lighting == "night" else 0.0
        if low_visibility is True:
            result.loc[:, "mean_vis"] = 1.0
        return result

    @staticmethod
    def _rank_scores(probabilities: np.ndarray, reference: np.ndarray) -> np.ndarray:
        probabilities = np.asarray(probabilities, dtype=float)
        left = np.searchsorted(reference, probabilities, side="left")
        right = np.searchsorted(reference, probabilities, side="right")
        average_rank = ((left + 1) + right) / 2.0
        return np.clip(average_rank / len(reference) * 100.0, 0.0, 100.0)

    @staticmethod
    def _rank_score(probability: float, reference: np.ndarray) -> float:
        return float(InferenceRuntime._rank_scores(np.asarray([probability]), reference)[0])

    @staticmethod
    def _confidence_scores(n_acc: np.ndarray, disagreement: np.ndarray,
                           threshold: float) -> np.ndarray:
        levels = np.where(n_acc >= 5, 2, np.where(n_acc >= 3, 1, 0))
        levels = np.where(disagreement > threshold, np.maximum(levels - 1, 0), levels)
        return np.choose(levels, np.asarray(["Low", "Medium", "High"]))

    @staticmethod
    def _override_key(overrides: Any | None) -> tuple[Any, ...]:
        """Preserve null versus explicit scenario inputs; the bounded LRU stays at 8 entries."""
        return (getattr(overrides, "weather_condition", None),
                getattr(overrides, "lighting", None),
                getattr(overrides, "low_visibility", None))

    @staticmethod
    def _active_condition_key(active_conditions: Iterable[str] | None) -> tuple[str, ...]:
        active = tuple(sorted(set(active_conditions or ())))
        if set(active) - set(CONDITIONS):
            raise ValueError("active_conditions may contain only rain, night, and rush.")
        return active

    def _condition_multiplier_vector(self, active_conditions: tuple[str, ...]) -> np.ndarray:
        if (not self.condition_lift_enabled or not config.CONDITION_LIFT_APPLIES_TO_RANKING
                or not active_conditions):
            return np.ones(len(self.features), dtype=float)
        multiplier = np.ones(len(self.features), dtype=float)
        for condition in active_conditions:
            multiplier *= self._condition_multiplier_vectors[condition]
        return np.minimum(multiplier, config.LIFT_CAP_TOTAL)

    def _condition_multiplier_for_cell(self, cell_id: str, active_conditions: tuple[str, ...]) -> float:
        if not self.condition_lift_enabled or not config.CONDITION_LIFT_APPLIES_TO_RANKING:
            return 1.0
        cell = self.condition_lift_cells.get(cell_id, {})
        condition_rows = cell.get("conditions", {})
        multiplier = 1.0
        for condition in active_conditions:
            row = condition_rows.get(condition, {}) if isinstance(condition_rows, dict) else {}
            if row.get("prone"):
                multiplier *= min(config.LIFT_CAP_PER_CONDITION, float(row.get("multiplier", 1.0)))
        return min(multiplier, config.LIFT_CAP_TOTAL)

    def _traffic_effect(self, cell_id: str, readings: dict[str, dict[str, Any]] | None
                        ) -> tuple[float, str | None, dict[str, Any] | None]:
        """Return an experimental learned multiplier and its transparent explanation."""
        reading = (readings or {}).get(cell_id)
        if not reading or reading.get("bucket") not in ("free", "moderate", "heavy"):
            return 1.0, None, None
        if self.traffic_lift is None:
            return 1.0, reading["bucket"], {
                "kind": "traffic_context",
                "reason": (f"Traffic here is {reading['bucket']} now. No learned congestion effect is loaded; "
                           "the score is unchanged."),
                "source": "Live traffic (TomTom)", "observed_at": reading.get("observed_at"),
            }
        multiplier, explanation = self.traffic_lift.adjustment(cell_id, reading)
        return multiplier, reading["bucket"], explanation

    def _model_scores(self, overrides: Any | None,
                      active_conditions: Iterable[str] | None = None
                      ) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
        active = self._active_condition_key(active_conditions)
        base_key = self._override_key(overrides)
        key = base_key if not active else (base_key, active)
        with self._cache_lock:
            cached = self._score_cache.get(key)
            if cached is not None:
                self._score_cache.move_to_end(key)
                return cached

        frame = self._apply_overrides(self.features, overrides)
        member_probs = np.column_stack([
            model.predict_proba(frame[self.feature_names])[:, 1] for model in self.models
        ])
        if not np.isfinite(member_probs).all():
            raise ValueError("Model returned a non-finite probability.")
        model_probabilities = member_probs.mean(axis=1)
        multiplier = self._condition_multiplier_vector(active)
        probabilities = np.minimum(1.0, model_probabilities * multiplier)
        disagreement = member_probs.std(axis=1)
        risk_scores = self._rank_scores(probabilities, self.probability_reference)
        confidence = self._confidence_scores(self.features["n_acc"].to_numpy(dtype=float),
                                             disagreement, self.disagreement_threshold).tolist()
        value = (probabilities, risk_scores, confidence, multiplier)
        with self._cache_lock:
            existing = self._score_cache.get(key)
            if existing is not None:
                self._score_cache.move_to_end(key)
                return existing
            self._score_cache[key] = value
            while len(self._score_cache) > self._score_cache_limit:
                self._score_cache.popitem(last=False)
        return value

    def warm_score_cache(self) -> None:
        """Populate baseline model-only arrays during application startup."""
        self._model_scores(None)

    def _confidence(self, n_acc: float, disagreement: float) -> str:
        level = 2 if n_acc >= 5 else (1 if n_acc >= 3 else 0)
        if disagreement > self.disagreement_threshold:
            level = max(level - 1, 0)
        return {0: "Low", 1: "Medium", 2: "High"}[level]

    def _incident_overlay_map(self, now: datetime | None = None) -> dict[str, tuple[float, int, int, float]]:
        scored_at = now or datetime.now(timezone.utc)
        with self._lock:
            snapshot = tuple(self._active_incidents.values())
        items = ({"cell_id": cell_id, "kind": kind, "status": status,
                  "occurred_at": occurred_at, "reporter_id": reporter_id}
                 for cell_id, kind, status, occurred_at, reporter_id in snapshot)
        return aggregate_details(items, scored_at)

    def _incident_overlay(self, cell_id: str, now: datetime | None = None) -> tuple[int, float, int, float]:
        points, count, independent, bonus = self._incident_overlay_map(now).get(cell_id, (0.0, 0, 0, 0.0))
        return count, points, independent, bonus

    def decorate_prediction(self, prediction: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """Refresh only the incident overlay without rerunning model inference or SHAP."""
        result = dict(prediction)
        scored_at = now or datetime.now(timezone.utc)
        count, points, independent, bonus = self._incident_overlay(str(result["cell_id"]), scored_at)
        result["active_incident_count"] = count
        result["incident_overlay_points"] = points
        result["live_risk_score"] = min(100.0, float(result["risk_score"]) + points)
        result["overlay_params"] = overlay_params()
        result["corroboration"] = {"report_count": count, "independent_reporters": independent,
                                   "bonus_points": bonus}
        return result

    def _serialize_prediction(self, cell_id: str, frame: pd.DataFrame, probabilities: np.ndarray,
                              disagreement: float, include_shap: bool,
                              now: datetime | None = None, *,
                              active_conditions: tuple[str, ...] = (),
                              condition_source: str = "historical Open-Meteo archive",
                              condition_observed_at: str | None = None,
                              condition_multiplier: float = 1.0,
                              traffic: tuple[float, str | None, dict[str, Any] | None] = (1.0, None, None)
                              ) -> dict[str, Any]:
        probability = float(probabilities.mean())
        risk_score = self._rank_score(probability, self.probability_reference)
        incident_count, overlay_points, independent_reporters, corroboration_bonus = self._incident_overlay(cell_id, now)
        adjustments = self._condition_adjustments(
            cell_id, active_conditions, condition_source, condition_observed_at)
        if traffic[2] is not None:
            adjustments.append(traffic[2])
        if overlay_points > 0:
            adjustments.append({
                "kind": "incident_overlay",
                "points": overlay_points,
                "reason": (f"+{corroboration_bonus:.1f} points from {incident_count} reports "
                           f"({independent_reporters} independent); incident reports are status-weighted and decayed."
                           if independent_reporters else
                           f"{incident_count} active incident report(s), status-weighted and decayed."),
                "source": "cell-level incident overlay",
                "observed_at": (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
            })
        output: dict[str, Any] = {
            "cell_id": cell_id,
            "risk_probability": probability,
            "risk_score": risk_score,
            "live_risk_score": min(100.0, risk_score + overlay_points),
            "confidence": self._confidence(float(frame.iloc[0]["n_acc"]), disagreement),
            "active_incident_count": incident_count,
            "incident_overlay_points": overlay_points,
            "corroboration": {"report_count": incident_count,
                              "independent_reporters": independent_reporters,
                              "bonus_points": corroboration_bonus},
            "condition_multiplier": condition_multiplier,
            "traffic_multiplier": traffic[0],
            "traffic_bucket": traffic[1],
            "adjustments": adjustments,
            "shap_contributors": [],
            "overlay_params": overlay_params(),
        }
        if include_shap:
            values = self.explainer.shap_values(frame[self.feature_names])
            if isinstance(values, list):
                values = values[1]
            values = np.asarray(values)
            if values.ndim == 3:
                values = values[:, :, 1]
            row = values[0]
            order = np.argsort(-np.abs(row))[:MAX_SHAP_CONTRIBUTORS]
            output["shap_contributors"] = [
                {
                    "feature": self.feature_names[j],
                    "label": FEATURE_LABELS.get(self.feature_names[j], self.feature_names[j]),
                    "feature_value": float(frame.iloc[0][self.feature_names[j]]),
                    "shap_value": float(row[j]),
                    "direction": "raises" if row[j] > 0 else "lowers",
                    "rank": rank,
                }
                for rank, j in enumerate(order, 1)
            ]
        return output

    def _condition_adjustments(self, cell_id: str, active_conditions: tuple[str, ...],
                               source: str, observed_at: str | None) -> list[dict[str, Any]]:
        if not active_conditions:
            return []
        cell = self.condition_lift_cells.get(cell_id, {})
        n = int(cell.get("n", 0))
        condition_rows = cell.get("conditions", {})
        adjustments = []
        raw_product = 1.0
        for condition in active_conditions:
            row = condition_rows.get(condition, {}) if isinstance(condition_rows, dict) else {}
            city_share = float(self.condition_citywide.get(condition, {}).get("p", 0.0))
            label = {"rain": "rain", "night": "night", "rush": "weekday rush hours"}[condition]
            has_evidence = self.condition_lift_enabled and bool(row.get("prone"))
            ranking_applied = has_evidence and config.CONDITION_LIFT_APPLIES_TO_RANKING
            if has_evidence:
                multiplier = float(row["multiplier"])
                raw_product *= multiplier
                k = int(row.get("k", 0))
                share = (k / n) if n else 0.0
                evidence = (f"{share:.0%} of this cell's {n} recorded crashes happened in {label}, "
                           f"versus {city_share:.0%} citywide; historically more crashes per hour in this condition.")
            elif row:
                evidence = (f"{label.capitalize()} is active. This cell does not meet the condition-lift evidence "
                            "threshold, so the condition does not change its score or ranking.")
            else:
                evidence = (f"{label.capitalize()} is active. Per-cell condition-lift evidence is unavailable; "
                            "the condition does not change this cell's score or ranking.")
            if not ranking_applied:
                evidence += " Context only, not a validated improvement; no score or ranking change is applied."
            adjustments.append({
                "kind": f"{condition}_{'lift' if ranking_applied else 'context'}",
                **({"multiplier": multiplier} if ranking_applied else {}),
                "reason": evidence,
                "source": source,
                "observed_at": observed_at,
            })
        if config.CONDITION_LIFT_APPLIES_TO_RANKING and raw_product > config.LIFT_CAP_TOTAL:
            adjustments.append({
                "kind": "condition_lift_cap",
                "multiplier": config.LIFT_CAP_TOTAL / raw_product,
                "reason": f"Combined condition multiplier is bounded at {config.LIFT_CAP_TOTAL:.2f}.",
                "source": source,
                "observed_at": observed_at,
            })
        return adjustments

    def predict(self, cell_id: str, overrides: Any | None = None, include_shap: bool = True,
                *, now: datetime | None = None,
                active_conditions: Iterable[str] | None = None,
                condition_source: str = "historical Open-Meteo archive",
                condition_observed_at: str | None = None,
                traffic_readings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        frame = self._apply_overrides(self.features.loc[[cell_id]], overrides)
        scored_at = now or datetime.now(timezone.utc)
        try:
            active = self._active_condition_key(active_conditions)
            member_probs = np.asarray([m.predict_proba(frame[self.feature_names])[0, 1] for m in self.models], dtype=float)
            if not np.isfinite(member_probs).all():
                raise ValueError("Model returned a non-finite probability.")
            condition_multiplier = self._condition_multiplier_for_cell(cell_id, active)
            traffic = self._traffic_effect(cell_id, traffic_readings)
            probability = min(1.0, float(member_probs.mean()) * condition_multiplier * traffic[0])
            return self._serialize_prediction(
                cell_id, frame, np.asarray([probability]), float(member_probs.std()), include_shap, scored_at,
                active_conditions=active, condition_source=condition_source,
                condition_observed_at=condition_observed_at,
                condition_multiplier=condition_multiplier, traffic=traffic)
        except Exception as exc:
            raise MLRuntimeError(f"Live prediction failed: {exc}") from exc

    def predict_batch(self, cell_ids: Iterable[str], overrides: Any | None = None,
                      *, now: datetime | None = None,
                      active_conditions: Iterable[str] | None = None,
                      traffic_readings: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(cell_ids))
        scored_at = now or datetime.now(timezone.utc)
        unknown = [cid for cid in ids if cid not in self._cell_positions]
        if unknown:
            raise KeyError(unknown[0])
        try:
            probabilities, risk_scores, confidence, multipliers = self._model_scores(overrides, active_conditions)
            positions = [self._cell_positions[cid] for cid in ids]
            probability_values = probabilities[positions].tolist()
            risk_values = risk_scores[positions].tolist()
            confidence_values = [confidence[i] for i in positions]
            multiplier_values = multipliers[positions].tolist()
            traffic_values: list[float] = []
            traffic_buckets: list[str | None] = []
            for index, cell_id in enumerate(ids):
                factor, bucket, _ = self._traffic_effect(cell_id, traffic_readings)
                traffic_values.append(factor)
                traffic_buckets.append(bucket)
                if factor != 1.0:
                    probability_values[index] = min(1.0, probability_values[index] * factor)
                    risk_values[index] = self._rank_score(probability_values[index], self.probability_reference)
            overlays = self._incident_overlay_map(scored_at)
            incident_counts: list[int] = []
            overlay_points: list[float] = []
            independent_reporters: list[int] = []
            corroboration_bonuses: list[float] = []
            for cell_id in ids:
                points, count, independent, bonus = overlays.get(cell_id, (0.0, 0, 0, 0.0))
                incident_counts.append(count)
                overlay_points.append(points)
                independent_reporters.append(independent)
                corroboration_bonuses.append(bonus)
            live_values = [min(100.0, risk + points) for risk, points in zip(risk_values, overlay_points)]
            return [
                {"cell_id": cell_id, "risk_probability": probability, "risk_score": risk,
                 "live_risk_score": live, "confidence": certainty,
                 "active_incident_count": incident_count, "incident_overlay_points": points,
                 "corroboration": {"report_count": incident_count,
                                   "independent_reporters": independent_count,
                                   "bonus_points": bonus_points},
                 "condition_multiplier": multiplier,
                 "traffic_multiplier": traffic_factor,
                 "traffic_bucket": traffic_bucket,
                 "shap_contributors": []}
                for cell_id, probability, risk, live, certainty, incident_count, points,
                    independent_count, bonus_points, multiplier, traffic_factor, traffic_bucket
                in zip(ids, probability_values, risk_values, live_values, confidence_values,
                       incident_counts, overlay_points, independent_reporters,
                       corroboration_bonuses, multiplier_values, traffic_values, traffic_buckets)
            ]
        except Exception as exc:
            raise MLRuntimeError(f"Batch prediction failed: {exc}") from exc

    def activate_incident(self, incident_id: int, cell_id: str, kind: str, *,
                          status: str = "pending", occurred_at: datetime | None = None,
                          reporter_id: str | None = None) -> None:
        incident_time = occurred_at or datetime.now(timezone.utc)
        with self._lock:
            self._active_incidents[int(incident_id)] = (cell_id, kind, status, incident_time,
                                                        str(reporter_id) if reporter_id is not None else None)

    def update_incident_status(self, incident_id: int, status: str) -> None:
        with self._lock:
            current = self._active_incidents.get(int(incident_id))
            if current is None:
                return
            if status == "rejected":
                del self._active_incidents[int(incident_id)]
                return
            cell_id, kind, _old_status, occurred_at, reporter_id = current
            self._active_incidents[int(incident_id)] = (cell_id, kind, status, occurred_at, reporter_id)

    def reject_incident(self, incident_id: int) -> None:
        self.update_incident_status(incident_id, "rejected")

    def active_incident_cell_ids(self) -> list[str]:
        """Return unique cells with at least one active report for snapshot hydration."""
        with self._lock:
            return list(dict.fromkeys(
                cell_id for cell_id, _kind, status, _occurred_at, _reporter_id in self._active_incidents.values()
                if status in ACTIVE_INCIDENT_STATUSES
                if cell_id in self._cell_positions
            ))

    def hydrate_incidents(self, rows: Iterable[Any], *, now: datetime | None = None) -> None:
        hydrated_at = now or datetime.now(timezone.utc)
        with self._lock:
            self._active_incidents = {
                int(row.id): (row.cell_id, row.kind, row.status, row.occurred_at,
                              str(getattr(row, "reported_by", None))
                              if getattr(row, "reported_by", None) is not None else None)
                for row in rows
                if row.status in ACTIVE_INCIDENT_STATUSES
                and effective_points(row.kind, row.status, row.occurred_at, hydrated_at) > 0
            }
            current = self._incident_overlay_map(hydrated_at)
            self._last_published_overlay_points = {cell: points for cell, (points, _count, _independent, _bonus) in current.items()}

    def mark_overlay_published(self, cell_id: str, points: float) -> None:
        with self._lock:
            self._last_published_overlay_points[cell_id] = float(points)

    def sweep_incidents(self, now: datetime | None = None) -> list[str]:
        """Expire stale reports and return cells whose published score needs an SSE refresh."""
        scored_at = now or datetime.now(timezone.utc)
        with self._lock:
            expired = [incident_id for incident_id, (_cell, kind, status, occurred, _reporter_id)
                       in self._active_incidents.items()
                       if effective_points(kind, status, occurred, scored_at) <= 0]
            for incident_id in expired:
                self._active_incidents.pop(incident_id, None)
            current = self._incident_overlay_map(scored_at)
            points_by_cell = {cell: points for cell, (points, _count, _independent, _bonus) in current.items()}
            changed: list[str] = []
            for cell_id in set(self._last_published_overlay_points) | set(points_by_cell):
                previous = self._last_published_overlay_points.get(cell_id, 0.0)
                current_points = points_by_cell.get(cell_id, 0.0)
                if abs(current_points - previous) >= SWEEP_PUBLISH_DELTA or (previous > 0 and current_points == 0):
                    changed.append(cell_id)
            return sorted(changed)
