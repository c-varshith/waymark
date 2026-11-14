#!/usr/bin/env python3
"""
Step 1. Download hourly Houston weather history (Open-Meteo archive API, no key needed).

Usage (from the project root):
    python pipeline/external/fetch_weather.py --start-year 2016 --end-year 2023

Writes data/external/weather_houston_hourly.csv (local Houston time, one row per hour).
Check the variable names against https://open-meteo.com/en/docs/historical-weather-api
before relying on them; the API can add or rename variables.
"""
import argparse, sys, time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = "precipitation,rain,weather_code,is_day,temperature_2m"


def fetch_range(client, lat, lon, start, end):
    params = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(),
              "end_date": end.isoformat(), "hourly": HOURLY, "timezone": "America/Chicago"}
    last = None
    for attempt in range(5):
        try:
            r = client.get(URL, params=params, timeout=60)
            if r.status_code == 429:           # rate limited: wait and retry
                time.sleep(20 * (attempt + 1)); continue
            r.raise_for_status()
            h = r.json()["hourly"]
            return pd.DataFrame(h)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Open-Meteo failed for {start}..{end}: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=29.76)
    ap.add_argument("--lon", type=float, default=-95.37)
    ap.add_argument("--start-year", type=int, default=2016)
    ap.add_argument("--end-year", type=int, default=2023)
    ap.add_argument("--out", default="data/external/weather_houston_hourly.csv")
    a = ap.parse_args()

    # the archive lags real time by a few days, so cap the last date
    cap = date.today() - timedelta(days=7)
    frames = []
    with httpx.Client(headers={"User-Agent": "WAYMARK-research/0.1"}) as client:
        for year in range(a.start_year, a.end_year + 1):
            start, end = date(year, 1, 1), min(date(year, 12, 31), cap)
            if start > end: break
            print(f"fetching {year} ...", flush=True)
            frames.append(fetch_range(client, a.lat, a.lon, start, end))
            time.sleep(1)                      # be polite
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    df = df.drop_duplicates("time").sort_values("time")

    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nwrote {out}  rows={len(df)}  {df.time.min()} .. {df.time.max()}")
    print("null counts:\n", df.isna().sum().to_string())
    # sanity: a full year is 8760 or 8784 rows
    per_year = df.groupby(df.time.dt.year).size()
    print("rows per year:\n", per_year.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
