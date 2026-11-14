"""TomTom traffic display: segment cache, flow-tile proxy and request budgets.

A separately learned experimental adjustment may consume fresh cached buckets in Live inference
after explicit opt-in.

Display paths remain separate from scoring. Only the in-memory watched-cell bucket can feed the
explicitly opted-in experimental Live adjustment:

* Watched segments. The background poller asks TomTom's Flow Segment Data API about the road
  segment at a few high-risk cells. Raw speeds are used transiently to derive a bucket and are
  never cached, logged, or persisted; the bucket and segment geometry remain in process memory.
  The bucket can affect Live inference only when a separately trained historical lift passes
  validation and the experimental operator flag is enabled. No provider reading is persisted.
* Street network. ``TrafficTileProxy`` relays TomTom raster flow tiles so every street in view is
  coloured by congestion. The key stays on the server; tiles are cached briefly and counted
  against a daily tile cap.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from starlette.concurrency import run_in_threadpool

from . import realtime_config as config
from .conditions import rush_active
from .db import SessionLocal
from .models import Cell, Crash
from .models_ops import TrafficRequestCounter

log = logging.getLogger("waymark.traffic")
FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
TRAFFIC_SOURCE = "TomTom Traffic Flow Segment Data"
HOUSTON_REF_LAT = 29.76
HOUSTON_REF_LNG = -95.37
RETENTION_DAYS = 90


def congestion_ratio(current_speed: float, free_flow_speed: float) -> float | None:
    if not math.isfinite(current_speed) or not math.isfinite(free_flow_speed) or free_flow_speed <= 0:
        return None
    return min(1.0, max(0.0, 1.0 - current_speed / free_flow_speed))


def congestion_bucket(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio < config.TRAFFIC_FREE_MAX:
        return "free"
    if ratio < config.TRAFFIC_MODERATE_MAX:
        return "moderate"
    return "heavy"


def quota_limits(monthly_quota: int | None = None,
                 budget_fraction: float | None = None,
                 daily_cap: int | None = None) -> tuple[int, int]:
    monthly_quota = config.TRAFFIC_MONTHLY_QUOTA if monthly_quota is None else monthly_quota
    budget_fraction = config.TRAFFIC_BUDGET_FRACTION if budget_fraction is None else budget_fraction
    daily_cap = config.TRAFFIC_DAILY_CAP if daily_cap is None else daily_cap
    return int(monthly_quota * budget_fraction), daily_cap


class TomTomTrafficProvider:
    """Minimal provider adapter. Never logs its URL because the key is a query parameter."""

    def __init__(self, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("WAYMARK_TRAFFIC_API_KEY")
        self.transport = transport

    async def flow(self, lat: float, lng: float) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Traffic provider key is not configured")
        async with httpx.AsyncClient(timeout=3.0, transport=self.transport) as client:
            response = await client.get(FLOW_URL, params={
                "key": self.api_key,
                "point": f"{lat:.6f},{lng:.6f}",
                "unit": "mph",
            })
            response.raise_for_status()
            body = response.json()
        row = body.get("flowSegmentData") if isinstance(body, dict) else None
        if not isinstance(row, dict):
            raise ValueError("TomTom response did not contain flow segment data")
        current = row.get("currentSpeed")
        free = row.get("freeFlowSpeed")
        confidence = row.get("confidence")
        frc = row.get("frc")
        road_closure = row.get("roadClosure")
        if (not isinstance(current, (int, float)) or not isinstance(free, (int, float))
                or not isinstance(confidence, (int, float)) or not isinstance(frc, str)
                or not isinstance(road_closure, bool)):
            raise ValueError("TomTom flow response fields were malformed")
        coords = (row.get("coordinates") or {}).get("coordinate", [])
        geometry = segment_geometry(coords)
        segment_key = None
        if geometry:
            compact = ";".join(f"{lat},{lng}" for lat, lng in geometry)
            segment_key = hashlib.sha256(compact.encode()).hexdigest()
        return {"current_speed": float(current), "free_flow_speed": float(free),
                "confidence": float(confidence), "frc": frc, "road_closure": road_closure,
                "segment_key": segment_key, "geometry": geometry}


MAX_SEGMENT_POINTS = 120


def segment_geometry(coords: Any, max_points: int = MAX_SEGMENT_POINTS) -> list[tuple[float, float]]:
    """TomTom segment coordinates -> [(lat, lng), ...], rounded to ~1 m and thinned to ``max_points``."""
    if not isinstance(coords, list):
        return []
    points: list[tuple[float, float]] = []
    for item in coords:
        if not isinstance(item, dict):
            continue
        lat, lng = item.get("latitude"), item.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        point = (round(float(lat), 6), round(float(lng), 6))
        if not points or points[-1] != point:
            points.append(point)
    if len(points) > max_points:
        step = (len(points) - 1) / (max_points - 1)
        points = [points[round(i * step)] for i in range(max_points)]
    return points if len(points) >= 2 else []


def densest_crash_points(cell_ids: list[str], rows: list[tuple[str, float, float]]) -> dict[str, tuple[float, float]]:
    """Pick the densest 100 m diameter crash spot in each cell and return its mean point."""
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    x_scale = 111_320.0 * math.cos(math.radians(HOUSTON_REF_LAT))
    y_scale = 111_320.0
    for cell_id, lat, lng in rows:
        if cell_id not in cell_ids:
            continue
        grouped[cell_id].append(((lng - HOUSTON_REF_LNG) * x_scale,
                                 (lat - HOUSTON_REF_LAT) * y_scale))
    points: dict[str, tuple[float, float]] = {}
    for cell_id, xy in grouped.items():
        best: list[tuple[float, float]] = []
        for x, y in xy:
            cluster = [(px, py) for px, py in xy if (px - x) ** 2 + (py - y) ** 2 <= 2_500]
            if len(cluster) > len(best):
                best = cluster
        points[cell_id] = (HOUSTON_REF_LAT + sum(p[1] for p in best) / len(best) / y_scale,
                           HOUSTON_REF_LNG + sum(p[0] for p in best) / len(best) / x_scale)
    return points


def _reserve_request_sync(now: datetime | None = None) -> tuple[bool, int, int]:
    """Count a request before attempting it; persisted counters survive app restarts."""
    current = now or datetime.now(timezone.utc)
    day_key = f"day:{current:%Y%m%d}"
    month_key = f"month:{current:%Y%m}"
    monthly_limit, daily_limit = quota_limits()
    with SessionLocal.begin() as session:
        dialect = session.bind.dialect.name if session.bind is not None else ""
        insert = sqlite_insert if dialect == "sqlite" else postgres_insert
        for key in (day_key, month_key):
            session.execute(insert(TrafficRequestCounter).values(period_key=key, request_count=0)
                            .on_conflict_do_nothing(index_elements=["period_key"]))
        day = session.scalar(select(TrafficRequestCounter).where(
            TrafficRequestCounter.period_key == day_key).with_for_update())
        month = session.scalar(select(TrafficRequestCounter).where(
            TrafficRequestCounter.period_key == month_key).with_for_update())
        assert day is not None and month is not None
        if day.request_count >= daily_limit or month.request_count >= monthly_limit:
            return False, day.request_count, month.request_count
        day.request_count += 1
        month.request_count += 1
        day_count, month_count = day.request_count, month.request_count
    return True, day_count, month_count


def _read_usage_sync(now: datetime | None = None) -> tuple[int, int]:
    current = now or datetime.now(timezone.utc)
    with SessionLocal() as session:
        day = session.get(TrafficRequestCounter, f"day:{current:%Y%m%d}")
        month = session.get(TrafficRequestCounter, f"month:{current:%Y%m}")
        return (day.request_count if day else 0, month.request_count if month else 0)


class TrafficManager:
    def __init__(self, *, provider: Any = None, broadcaster: Any = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 poll_seconds: float | None = None):
        self.provider = provider or TomTomTrafficProvider()
        self.broadcaster = broadcaster
        self.clock = clock
        self.poll_seconds = poll_seconds or config.TRAFFIC_POLL_MINUTES * 60
        self.candidates: list[tuple[str, float, float, float]] = []  # cell, score, lat, lng
        self.watchlist: list[str] = []
        self.readings: dict[str, dict[str, Any]] = {}  # bucket + fetch timestamp only
        self._queue: list[str] = []
        self._rejected_cells: set[str] = set()
        self._last_on_demand: dict[str, datetime] = {}
        self._last_event: dict[str, tuple[str | None, datetime]] = {}
        self._wake_event = asyncio.Event()
        self._failures = 0
        self._circuit_until: datetime | None = None
        self._budget_reached = False
        self._last_poll: datetime | None = None
        self._last_error: str | None = None
        self._day_count = 0
        self._month_count = 0
        self._last_request_monotonic = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None

    def configure_watchlist(self, runtime: Any) -> int:
        """Build ranked candidate coordinates for the highest-risk cells.

        The densest historical crash spot in a cell is preferred because it sits on the road where crashes
        happen. When crash points were never imported the cell centre is used instead; TomTom snaps a point
        to the nearest road segment and minor roads (FRC5+) are rejected during polling.
        """
        try:
            with SessionLocal() as session:
                cells = session.scalars(select(Cell).where(Cell.n_past_crashes > 0)
                                        .order_by(Cell.risk_score.desc().nullslast())).all()
                ids = [cell.cell_id for cell in cells[: max(64, config.TRAFFIC_WATCH_SIZE * 8)]]
                active = runtime.active_incident_cell_ids() if runtime is not None else []
                if active:
                    active_rows = session.scalars(select(Cell).where(Cell.cell_id.in_(active))).all()
                    by_id = {cell.cell_id: cell for cell in cells}
                    by_id.update({cell.cell_id: cell for cell in active_rows})
                    ids = list(dict.fromkeys(active + ids))[: max(64, config.TRAFFIC_WATCH_SIZE * 8)]
                    cells_by_id = by_id
                else:
                    cells_by_id = {cell.cell_id: cell for cell in cells[: max(64, config.TRAFFIC_WATCH_SIZE * 8)]}
        except SQLAlchemyError:
            log.warning("Traffic watch list unavailable: scored cells could not be read")
            self.candidates = []
            self.watchlist = []
            return 0
        crash_rows: list[Any] = []
        if ids:
            try:
                with SessionLocal() as session:
                    crash_rows = session.execute(select(Crash.cell_id, Crash.lat, Crash.lng)
                                                 .where(Crash.cell_id.in_(ids))).all()
            except SQLAlchemyError:
                log.info("Crash points are not loaded; traffic probes use cell centres")
        points = densest_crash_points(ids, [(cid, float(lat), float(lng)) for cid, lat, lng in crash_rows])
        for cid in ids:
            cell = cells_by_id.get(cid)
            if cid not in points and cell is not None and cell.lat is not None and cell.lng is not None:
                points[cid] = (float(cell.lat), float(cell.lng))
        active_order = {cid: index for index, cid in enumerate(active)}
        lift = getattr(runtime, "traffic_lift", None)
        sensitive = lift.sensitive_cells() if lift is not None and lift.applied else set()
        self.candidates = []
        for cid in ids:
            cell = cells_by_id.get(cid)
            point = points.get(cid)
            if cell is None or point is None:
                continue
            score = float(cell.risk_score or 0)
            if cid in sensitive:
                score += 200.0  # Prioritise cells where an experimental effect needs a fresh reading.
            if cid in active_order:
                score += 1000.0 - active_order[cid]
            self.candidates.append((cid, score, point[0], point[1]))
        self.candidates.sort(key=lambda item: item[1], reverse=True)
        self.watchlist = [row[0] for row in self.candidates[:config.TRAFFIC_WATCH_SIZE]]
        return len(self.candidates)

    def enqueue_cell(self, cell_id: str) -> bool:
        now = self.clock()
        last = self._last_on_demand.get(cell_id)
        if last and now - last < timedelta(minutes=config.TRAFFIC_ON_DEMAND_COOLDOWN_MINUTES):
            return False
        if not any(row[0] == cell_id for row in self.candidates):
            return False
        self._last_on_demand[cell_id] = now
        if cell_id not in self._queue:
            self._queue.append(cell_id)
        self._wake_event.set()
        return True

    def notify_clients_changed(self) -> None:
        """Wake the poller promptly when a stream client connects."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake_event.set)

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        budget = self._budget_reached
        fresh_seconds = config.TRAFFIC_FRESH_MINUTES * 60
        stale_after = timedelta(hours=24)
        for cell_id, reading in list(self.readings.items()):
            age = max(0.0, (now - reading["_observed_dt"]).total_seconds())
            if now - reading["_observed_dt"] > stale_after:
                del self.readings[cell_id]
        any_cache = bool(self.readings)
        last_fetch = max((row["_observed_dt"] for row in self.readings.values()), default=None)
        if budget:
            status = "budget_reached"
        elif last_fetch is None:
            status = "unavailable"
        elif (now - last_fetch).total_seconds() <= fresh_seconds:
            status = "ok"
        else:
            status = "stale"
        cells = []
        if status in {"ok", "stale", "budget_reached"}:
            for cid, row in self.readings.items():
                cells.append({"cell_id": cid, "bucket": row["bucket"],
                              "observed_at": row["observed_at"],
                              "road_closure": row["road_closure"],
                              "geometry": [list(point) for point in row.get("geometry") or []],
                              "status": "ok" if status == "ok" and (now - row["_observed_dt"]).total_seconds() <= fresh_seconds else "stale"})
        return {
            "status": status,
            "source": TRAFFIC_SOURCE,
            "attribution": config.TRAFFIC_ATTRIBUTION,
            "cells": cells,
            "watch_cells": self.watchlist,
            "observed_at": last_fetch.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if last_fetch else None,
            "requests_today": self._day_count,
            "requests_month": self._month_count,
            "daily_cap": config.TRAFFIC_DAILY_CAP,
            "monthly_cap": quota_limits()[0],
            "score_effect": False,
            "error": self._last_error,
            "tiles": tile_proxy_info(self.provider),
        }

    def live_readings(self) -> dict[str, dict[str, Any]]:
        """Return only fresh per-cell buckets and timestamps for Live inference; never speeds."""
        now = self.clock()
        fresh = timedelta(minutes=config.TRAFFIC_FRESH_MINUTES)
        return {cid: {"bucket": row["bucket"], "observed_at": row["observed_at"]}
                for cid, row in list(self.readings.items()) if now - row["_observed_dt"] <= fresh}

    async def _reserve(self) -> bool:
        allowed, day, month = await run_in_threadpool(_reserve_request_sync, self.clock())
        self._day_count, self._month_count = day, month
        if not allowed:
            self._budget_reached = True
        return allowed

    async def refresh_usage(self) -> None:
        self._day_count, self._month_count = await run_in_threadpool(_read_usage_sync, self.clock())
        self._budget_reached = (self._day_count >= config.TRAFFIC_DAILY_CAP
                                or self._month_count >= quota_limits()[0])

    async def _fetch(self, cell_id: str, lat: float, lng: float) -> tuple[dict[str, Any] | None, bool]:
        interval = 1.0 / max(0.1, config.TRAFFIC_MAX_QPS)
        delay = interval - (time.monotonic() - self._last_request_monotonic)
        if delay > 0:
            await asyncio.sleep(delay)
        if not await self._reserve():
            return None, False
        self._last_request_monotonic = time.monotonic()
        reading = await self.provider.flow(lat, lng)
        if reading["confidence"] < config.TRAFFIC_MIN_CONFIDENCE:
            return None, True
        ratio = congestion_ratio(reading["current_speed"], reading["free_flow_speed"])
        bucket = congestion_bucket(ratio)
        if bucket is None:
            return None, True
        now = self.clock()
        stamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {"bucket": bucket, "observed_at": stamp, "_observed_dt": now,
                "road_closure": bool(reading["road_closure"]),
                "frc": reading["frc"], "segment_key": reading.get("segment_key"),
                "geometry": list(reading.get("geometry") or [])}, True

    async def poll_once(self) -> dict[str, Any]:
        now = self.clock()
        await self.refresh_usage()
        if self.broadcaster is None or self.broadcaster.subscriber_count <= 0:
            self._last_poll = now
            return self.snapshot()
        if not getattr(self.provider, "api_key", None):
            self._last_error = "Traffic provider key is not configured"
            return self.snapshot()
        if self._budget_reached:
            return self.snapshot()
        if self._circuit_until and now < self._circuit_until:
            return self.snapshot()
        if self._circuit_until and now >= self._circuit_until:
            self._circuit_until = None
            self._failures = 0

        candidate_by_id = {row[0]: row for row in self.candidates}
        pending = [cid for cid in self._queue if cid in candidate_by_id]
        self._queue = [cid for cid in self._queue if cid not in pending]
        ids = list(dict.fromkeys(pending + self.watchlist))
        selected: list[str] = []
        seen_segments: set[str] = set()
        # Verify candidates in risk order, replacing unhelpful FRC5/6 matches and duplicate segments.
        for cid, _, lat, lng in [candidate_by_id[cid] for cid in ids if cid in candidate_by_id] + [
            row for row in self.candidates if row[0] not in ids and row[0] not in self._rejected_cells
        ]:
            if len(selected) >= config.TRAFFIC_WATCH_SIZE:
                break
            if cid in self._rejected_cells:
                continue
            try:
                row, attempted = await self._fetch(cid, lat, lng)
            except Exception as exc:
                self._failures += 1
                self._last_error = type(exc).__name__
                if self._failures >= config.TRAFFIC_CIRCUIT_FAILURES:
                    self._circuit_until = now + timedelta(minutes=config.TRAFFIC_CIRCUIT_RESET_MINUTES)
                log.warning("TomTom traffic refresh failed (%d consecutive): %s", self._failures, type(exc).__name__)
                if self._circuit_until:
                    break
                continue
            if not attempted:
                break
            if row is None:
                continue
            self._failures = 0
            self._last_error = None
            self._last_poll = now
            frc_num = int(row["frc"].removeprefix("FRC")) if row["frc"].startswith("FRC") else 99
            if frc_num >= 5:
                self._rejected_cells.add(cid)
                continue
            segment = row.get("segment_key")
            if segment and segment in seen_segments:
                self._rejected_cells.add(cid)
                continue
            if segment:
                seen_segments.add(segment)
            old = self.readings.get(cid)
            self.readings[cid] = {key: row[key] for key in ("bucket", "observed_at", "_observed_dt", "road_closure", "geometry")}
            selected.append(cid)
            last_event = self._last_event.get(cid)
            if self.broadcaster and (old is None or old["bucket"] != row["bucket"]):
                if last_event is None or now - last_event[1] >= timedelta(minutes=1):
                    self.broadcaster.publish("traffic.updated", {"cell_id": cid,
                                                                  "bucket": row["bucket"],
                                                                  "observed_at": row["observed_at"]})
                    self._last_event[cid] = (row["bucket"], now)
        self.watchlist = selected
        return self.snapshot()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.refresh_usage()
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._last_error = "Traffic poll failed"
                log.exception("Traffic polling task failed")
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
            finally:
                self._wake_event.clear()


TILE_URL = "https://api.tomtom.com/traffic/map/4/tile/flow/{style}/{z}/{x}/{y}.png"
TILE_PATH = "/api/live/traffic/tiles/{z}/{x}/{y}.png"


class TileBudgetExceeded(RuntimeError):
    pass


class TrafficTileProxy:
    """Relay TomTom raster flow tiles so the browser never sees the key.

    Tiles are cached for ``ttl`` seconds (TomTom refreshes flow about once a minute) and fresh
    upstream fetches are counted against a per-UTC-day cap that stays under the free tile quota.
    """

    def __init__(self, *, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.api_key = api_key if api_key is not None else os.getenv("WAYMARK_TRAFFIC_API_KEY")
        self.transport = transport
        self.clock = clock
        self.ttl = config.TRAFFIC_TILE_TTL_SECONDS
        self.max_entries = config.TRAFFIC_TILE_CACHE_SIZE
        self._cache: "OrderedDict[tuple[int, int, int], tuple[float, bytes]]" = OrderedDict()
        self._day: str | None = None
        self._count = 0
        self._inflight: dict[tuple[int, int, int], asyncio.Future[bytes]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and config.TRAFFIC_TILES_ENABLED

    def budget_left(self) -> int:
        day = f"{self.clock():%Y%m%d}"
        if day != self._day:
            self._day, self._count = day, 0
        return max(0, config.TRAFFIC_TILE_DAILY_CAP - self._count)

    @staticmethod
    def valid(z: int, x: int, y: int) -> bool:
        if not config.TRAFFIC_TILE_MIN_ZOOM <= z <= config.TRAFFIC_TILE_MAX_ZOOM:
            return False
        n = 1 << z
        return 0 <= x < n and 0 <= y < n

    async def tile(self, z: int, x: int, y: int) -> bytes:
        key = (z, x, y)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] <= self.ttl:
            self._cache.move_to_end(key)
            return hit[1]
        pending = self._inflight.get(key)
        if pending is not None:                      # many viewers asking for the same tile share one fetch
            return await asyncio.shield(pending)
        if self.budget_left() <= 0:
            if hit:                                   # serve the last copy rather than nothing
                return hit[1]
            raise TileBudgetExceeded("Daily traffic tile budget reached")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        self._inflight[key] = future
        try:
            self._count += 1
            params = {"key": self.api_key, "tileSize": 256}
            if config.TRAFFIC_TILE_THICKNESS:
                params["thickness"] = config.TRAFFIC_TILE_THICKNESS
            async with httpx.AsyncClient(timeout=5.0, transport=self.transport) as client:
                response = await client.get(TILE_URL.format(style=config.TRAFFIC_TILE_STYLE, z=z, x=x, y=y),
                                            params=params)
                response.raise_for_status()
                body = response.content
            if not body.startswith(b"\x89PNG"):
                raise ValueError("TomTom tile response was not a PNG")
            self._cache[key] = (time.monotonic(), body)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            future.set_result(body)
            return body
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()                     # mark retrieved so asyncio does not warn
            raise
        finally:
            self._inflight.pop(key, None)


def tile_proxy_info(provider: Any = None) -> dict[str, Any]:
    """What the map needs to draw the street-level traffic tile layer."""
    key = getattr(provider, "api_key", None) if provider is not None else None
    key = key if key is not None else os.getenv("WAYMARK_TRAFFIC_API_KEY")
    return {
        "available": bool(key) and config.TRAFFIC_TILES_ENABLED,
        "url": TILE_PATH,
        "min_zoom": config.TRAFFIC_TILE_MIN_ZOOM,
        "max_zoom": config.TRAFFIC_TILE_MAX_ZOOM,
    }


class ConditionHistoryWriter:
    """Append cell-only snapshots on a 15-minute cadence; provider IO is never involved."""

    def __init__(self, *, interval_minutes: int = 15, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.interval_seconds = interval_minutes * 60
        self.clock = clock
        self._last_prune: date | None = None

    def write_once(self, runtime: Any, context_manager: Any, traffic_manager: TrafficManager) -> int:
        from .models_ops import ConditionSnapshot
        today = self.clock().date()
        if self._last_prune != today:
            with SessionLocal.begin() as session:
                prune_condition_history(session, self.clock())
            self._last_prune = today
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        context = context_manager.snapshot(now) if context_manager is not None else None
        cell_ids = traffic_manager.watchlist[:config.TRAFFIC_WATCH_SIZE]
        if not cell_ids:
            return 0
        predictions = runtime.predict_batch(cell_ids)
        weather_ok = bool(context and context.get("status") == "ok")
        active = set(context.get("active_conditions", [])) if context else set()
        local_now = now.astimezone(ZoneInfo(config.HOUSTON_TIMEZONE))
        at = now.astimezone(timezone.utc).replace(tzinfo=None)
        rows = [ConditionSnapshot(
            cell_id=item["cell_id"], at=at,
            rain=("rain" in active) if weather_ok else None,
            night=("night" in active) if weather_ok else None,
            rush=("rush" in active) if context else rush_active(local_now),
            # Live traffic is display-only. TomTom terms currently block historic feed collection.
            congestion_bucket=None,
            base_score=float(item["risk_score"]), live_score=float(item["live_risk_score"]),
            active_incident_count=int(item["active_incident_count"]),
            model_version=runtime.model_version,
            condition_lift_version=runtime.condition_lift_version,
        ) for item in predictions]
        with SessionLocal.begin() as session:
            session.add_all(rows)
        return len(rows)

    async def run(self, runtime_getter: Callable[[], Any], context_getter: Callable[[], Any],
                  traffic_getter: Callable[[], TrafficManager]) -> None:
        while True:
            runtime = runtime_getter()
            if runtime is not None:
                try:
                    await run_in_threadpool(self.write_once, runtime, context_getter(), traffic_getter())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Could not write cell-level condition snapshots")
            await asyncio.sleep(self.interval_seconds)


def prune_condition_history(session: Any, now: datetime | None = None) -> int:
    """Delete condition snapshots older than the fixed 90-day retention period."""
    from .models_ops import ConditionSnapshot
    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(days=RETENTION_DAYS)).replace(tzinfo=None)
    result = session.execute(delete(ConditionSnapshot).where(ConditionSnapshot.at < cutoff))
    return int(result.rowcount or 0)
