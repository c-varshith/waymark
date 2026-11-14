#!/usr/bin/env python3
"""Export retained cell-level condition snapshots to CSV for future modelling."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sqlalchemy import select

from backend.db import SessionLocal
from backend.models_ops import ConditionSnapshot

FIELDS = ["cell_id", "at", "rain", "night", "rush", "congestion_bucket", "base_score",
          "live_score", "active_incident_count", "model_version", "condition_lift_version"]


def write_csv(rows, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: getattr(row, field) for field in FIELDS})
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("condition_snapshots.csv"))
    args = parser.parse_args()
    with SessionLocal() as session:
        rows = session.scalars(select(ConditionSnapshot).order_by(ConditionSnapshot.at, ConditionSnapshot.cell_id)).all()
        count = write_csv(rows, args.output)
    print(f"Exported {count:,} cell-level condition rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
