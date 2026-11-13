"""In-memory inference runtime for the packaged WAYMARK model."""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

import h3
import joblib
import numpy as np
import pandas as pd
import shap

from .services import FEATURE_LABELS

MAX_SHAP_CONTRIBUTORS = 6
ACTIVE_INCIDENT_STATUSES = frozenset({"pending", "confirmed"})
CRASH_OVERLAY_POINTS = 20.0
NEAR_MISS_OVERLAY_POINTS = 10.0


class MLRuntimeError(RuntimeError):
    """The packaged model or its baseline feature store is invalid or unavailable."""


class InferenceRuntime:
    """Loaded model, explainer, feature vectors and process-local live incident state."""

    def __init__(self, bundle: dict[str, Any], schema: dict[str, Any], explainer: Any,
                 features: pd.DataFrame, disagreement_threshold: float):
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
        self._active_incidents: dict[int, tuple[str, str]] = {}
        self._lock = threading.RLock()
        self._score_cache: OrderedDict[tuple[Any, ...], tuple[np.ndarray, np.ndarray, list[str]]] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._score_cache_limit = 8

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
        runtime = cls(bundle, schema, explainer, frame, disagreement_threshold)
        runtime.model_version = "lgbm3-" + hashlib.sha256(model_path.read_bytes()).hexdigest()[:8]
        return runtime

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

    def _model_scores(self, overrides: Any | None) -> tuple[np.ndarray, np.ndarray, list[str]]:
        key = self._override_key(overrides)
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
        probabilities = member_probs.mean(axis=1)
        disagreement = member_probs.std(axis=1)
        risk_scores = self._rank_scores(probabilities, self.probability_reference)
        confidence = self._confidence_scores(self.features["n_acc"].to_numpy(dtype=float),
                                             disagreement, self.disagreement_threshold).tolist()
        value = (probabilities, risk_scores, confidence)
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

    def _incident_overlay_map(self) -> dict[str, tuple[int, float]]:
        with self._lock:
            snapshot = tuple(self._active_incidents.values())
        totals: dict[str, tuple[int, float]] = {}
        for cell_id, kind in snapshot:
            count, points = totals.get(cell_id, (0, 0.0))
            points += CRASH_OVERLAY_POINTS if kind == "crash" else NEAR_MISS_OVERLAY_POINTS
            totals[cell_id] = (count + 1, min(points, 100.0))
        return totals

    def _incident_overlay(self, cell_id: str) -> tuple[int, float]:
        return self._incident_overlay_map().get(cell_id, (0, 0.0))

    def decorate_prediction(self, prediction: dict[str, Any]) -> dict[str, Any]:
        """Refresh only the incident overlay without rerunning model inference or SHAP."""
        result = dict(prediction)
        count, points = self._incident_overlay(str(result["cell_id"]))
        result["active_incident_count"] = count
        result["incident_overlay_points"] = points
        result["live_risk_score"] = min(100.0, float(result["risk_score"]) + points)
        return result

    def _serialize_prediction(self, cell_id: str, frame: pd.DataFrame, probabilities: np.ndarray,
                              disagreement: float, include_shap: bool) -> dict[str, Any]:
        probability = float(probabilities.mean())
        risk_score = self._rank_score(probability, self.probability_reference)
        incident_count, overlay_points = self._incident_overlay(cell_id)
        output: dict[str, Any] = {
            "cell_id": cell_id,
            "risk_probability": probability,
            "risk_score": risk_score,
            "live_risk_score": min(100.0, risk_score + overlay_points),
            "confidence": self._confidence(float(frame.iloc[0]["n_acc"]), disagreement),
            "active_incident_count": incident_count,
            "incident_overlay_points": overlay_points,
            "shap_contributors": [],
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

    def predict(self, cell_id: str, overrides: Any | None = None, include_shap: bool = True) -> dict[str, Any]:
        frame = self._apply_overrides(self.features.loc[[cell_id]], overrides)
        try:
            member_probs = np.asarray([m.predict_proba(frame[self.feature_names])[0, 1] for m in self.models], dtype=float)
            if not np.isfinite(member_probs).all():
                raise ValueError("Model returned a non-finite probability.")
            return self._serialize_prediction(cell_id, frame, member_probs, float(member_probs.std()), include_shap)
        except Exception as exc:
            raise MLRuntimeError(f"Live prediction failed: {exc}") from exc

    def predict_batch(self, cell_ids: Iterable[str], overrides: Any | None = None) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(cell_ids))
        unknown = [cid for cid in ids if cid not in self._cell_positions]
        if unknown:
            raise KeyError(unknown[0])
        try:
            probabilities, risk_scores, confidence = self._model_scores(overrides)
            positions = [self._cell_positions[cid] for cid in ids]
            probability_values = probabilities[positions].tolist()
            risk_values = risk_scores[positions].tolist()
            confidence_values = [confidence[i] for i in positions]
            overlays = self._incident_overlay_map()
            incident_counts: list[int] = []
            overlay_points: list[float] = []
            for cell_id in ids:
                count, points = overlays.get(cell_id, (0, 0.0))
                incident_counts.append(count)
                overlay_points.append(points)
            live_values = [min(100.0, risk + points) for risk, points in zip(risk_values, overlay_points)]
            return [
                {"cell_id": cell_id, "risk_probability": probability, "risk_score": risk,
                 "live_risk_score": live, "confidence": certainty,
                 "active_incident_count": incident_count, "incident_overlay_points": points,
                 "shap_contributors": []}
                for cell_id, probability, risk, live, certainty, incident_count, points
                in zip(ids, probability_values, risk_values, live_values, confidence_values,
                       incident_counts, overlay_points)
            ]
        except Exception as exc:
            raise MLRuntimeError(f"Batch prediction failed: {exc}") from exc

    def activate_incident(self, incident_id: int, cell_id: str, kind: str) -> None:
        with self._lock:
            self._active_incidents[int(incident_id)] = (cell_id, kind)

    def reject_incident(self, incident_id: int) -> None:
        with self._lock:
            self._active_incidents.pop(int(incident_id), None)

    def hydrate_incidents(self, rows: Iterable[Any]) -> None:
        with self._lock:
            self._active_incidents = {
                int(row.id): (row.cell_id, row.kind)
                for row in rows
                if row.status in ACTIVE_INCIDENT_STATUSES
            }
