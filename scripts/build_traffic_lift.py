#!/usr/bin/env python3
"""Learn how congestion changes crash risk, check it on a held-out year, and write the runtime artifact.

    python scripts/build_traffic_lift.py --traffic data/external/houston_traffic_hourly.csv
    python scripts/build_traffic_lift.py --traffic traffic.csv --csv US_Accidents_March23.csv --holdout-year 2022

Traffic file: one row per road segment (or cell) and hour. See docs/traffic_lift.md for the columns
and for where such history can be obtained. Crashes come from the database's ``crashes`` table
(pipeline/load_crashes.py) unless ``--csv`` points at the US Accidents CSV.

The artifact records the held-out verdict. The API can apply the effect to Live scores only when
the verdict is PASS and WAYMARK_TRAFFIC_LIFT=1 is explicitly set (the default is off); otherwise it
is shown as context only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.traffic_lift import build_document, crash_hours, load_traffic_archive, write_document  # noqa: E402


def load_crashes(args: argparse.Namespace) -> pd.DataFrame:
    if args.csv:
        import blackspot_pipeline as pipeline
        df = pipeline.load(SimpleNamespace(csv=args.csv, state=args.state, city=args.city, res=9))
        return crash_hours(df[["cell", "Start_Time"]])
    db = Path(args.db)
    if not db.exists():
        sys.exit(f"{db} not found. Pass --csv US_Accidents_March23.csv or run pipeline/load_db.py first.")
    with sqlite3.connect(db) as con:
        try:
            df = pd.read_sql_query("SELECT cell_id, start_time FROM crashes WHERE start_time IS NOT NULL", con)
        except Exception:
            df = pd.DataFrame()
    if df.empty:
        sys.exit("The crashes table is empty. Run pipeline/load_crashes.py or pass --csv.")
    return crash_hours(df)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traffic", required=True, help="historical hourly traffic CSV")
    ap.add_argument("--csv", help="US Accidents CSV (default: the database crashes table)")
    ap.add_argument("--db", default="waymark.db")
    ap.add_argument("--state", default="TX")
    ap.add_argument("--city", default="Houston")
    ap.add_argument("--holdout-year", type=int, default=None, help="default: the last year in the traffic file")
    ap.add_argument("--out", default="backend/ml_artifacts/traffic_lift.json")
    args = ap.parse_args()

    archive = load_traffic_archive(pd.read_csv(args.traffic))
    crashes = load_crashes(args)
    document = build_document(crashes, archive, args.holdout_year,
                              source={"traffic_file": Path(args.traffic).name,
                                      "crash_source": args.csv and Path(args.csv).name or "database crashes table"})
    version = write_document(document, Path(args.out))
    v = document["validation"]
    print(json.dumps({
        "traffic_lift_version": version, "out": args.out,
        "verdict": v["verdict"], "reason": v.get("reason"),
        "holdout_year": v.get("holdout_year"), "holdout_crashes": v.get("holdout_crashes"),
        "loglik_gain_per_crash": v.get("gain_per_crash"), "ci95": v.get("ci"),
        "citywide_multipliers": {b: round(r["multiplier"], 3) for b, r in document["citywide"].items()},
        "cells_with_own_effect": len(document["cells"]),
        "matched_crashes": document["source"]["matched_crashes"],
    }, indent=2))
    if v["verdict"] != "PASS":
        print("\nNot applied to live scores until the held-out check passes; restart the API to load the artifact.")
    else:
        print("\nValidation passed. The effect is still off by default; review the result, set WAYMARK_TRAFFIC_LIFT=1 to opt in, then restart the API.")


if __name__ == "__main__":
    main()
