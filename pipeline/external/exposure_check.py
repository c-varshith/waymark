#!/usr/bin/env python3
"""
Step 2. Build the exposure baseline and sanity-check the weather archive against your crashes.

Usage:
    python pipeline/external/exposure_check.py --csv US_Accidents_March23.csv
    python pipeline/external/exposure_check.py --csv US_Accidents_March23.csv --sweep 0.1,0.3,0.5,1,2
    python pipeline/external/exposure_check.py --csv US_Accidents_March23.csv --rain-mm 0.5

The weather hours are restricted to the period the crashes actually cover (first to last crash), so a
partial first or last year does not distort the shares.

Prints, per year and overall:
  - the share of HOURS that were rainy / dark (exposure)
  - the share of CRASHES in rain / dark, using the archive and using the dataset's own flags
  - lift = crash share / hour share (a crude per-hour rate ratio, not a causal effect)
  - how often the dataset's flag and the archive agree
--sweep prints one overall row per rain threshold so you can pick RAIN_MM from data.
Writes data/external/exposure_baseline.csv (the table for --rain-mm).
"""
import argparse, sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import realtime_config as config
from backend.conditions import historical_flags


def agreement_metrics(reference, observed):
    """Return positive-class precision/recall and Cohen's kappa for boolean flags."""
    reference = np.asarray(reference, dtype=bool)
    observed = np.asarray(observed, dtype=bool)
    tp = int(np.count_nonzero(reference & observed))
    fp = int(np.count_nonzero(~reference & observed))
    fn = int(np.count_nonzero(reference & ~observed))
    precision = tp / (tp + fp) if tp + fp else np.nan
    recall = tp / (tp + fn) if tp + fn else np.nan
    n = len(reference)
    if not n:
        return precision, recall, np.nan
    observed_agreement = float(np.mean(reference == observed))
    p_ref = float(np.mean(reference))
    p_obs = float(np.mean(observed))
    expected_agreement = p_ref * p_obs + (1 - p_ref) * (1 - p_obs)
    kappa = ((observed_agreement - expected_agreement) / (1 - expected_agreement)
             if expected_agreement < 1 else np.nan)
    return precision, recall, kappa

COLS = ["Start_Time", "State", "City", "Weather_Condition", "Precipitation(in)", "Sunrise_Sunset"]


def load_crashes(a, w):
    c = pd.read_csv(a.csv, usecols=lambda x: x in COLS)
    c["Start_Time"] = pd.to_datetime(c["Start_Time"], format="mixed", errors="coerce")
    c = c.dropna(subset=["Start_Time"])
    c = c[(c.State == a.state) & (c.City == a.city)].copy()
    c["hour"] = c.Start_Time.dt.floor("h"); c["year"] = c.Start_Time.dt.year
    wc = c.get("Weather_Condition", pd.Series("", index=c.index)).fillna("").str.lower()
    pr = c.get("Precipitation(in)", pd.Series(0, index=c.index)).fillna(0)
    # Dataset labels are retained only for archive-agreement diagnostics. Lift tags below
    # always come from the Open-Meteo archive and shared condition definitions.
    c["rain_ds"] = wc.str.contains("rain|drizzle|storm|shower") | (pr > 0)
    c["night_ds"] = (c.get("Sunrise_Sunset", "Day") == "Night")
    return c


def table(w, c, rain_mm):
    w = w.copy()
    w["rain_hr"] = (w["precipitation"].fillna(0) >= rain_mm)
    w["night_hr"] = (w["is_day"] == 0)
    w["year"] = w.time.dt.year
    m = c.merge(w[["time", "rain_hr", "night_hr"]], left_on="hour", right_on="time", how="inner")
    m["rain_hr"] = m.rain_hr.astype(bool); m["night_hr"] = m.night_hr.astype(bool)
    rows = []
    for yr, g in list(m.groupby("year")) + [("all", m)]:
        wy = w if yr == "all" else w[w.year == yr]
        h_rain, h_night = wy.rain_hr.mean(), wy.night_hr.mean()
        # Treat the dataset's flags as reference labels and the archive as the measurement.
        rain_precision, rain_recall, rain_kappa = agreement_metrics(g.rain_ds, g.rain_hr)
        night_precision, night_recall, night_kappa = agreement_metrics(g.night_ds, g.night_hr)
        rows.append({
            "year": yr, "crashes": len(g),
            "hours_rain_share": h_rain, "crash_rain_share_archive": g.rain_hr.mean(),
            "crash_rain_share_dataset": g.rain_ds.mean(),
            "rain_lift_archive": g.rain_hr.mean() / h_rain if h_rain else np.nan,
            "hours_night_share": h_night, "crash_night_share_archive": g.night_hr.mean(),
            "crash_night_share_dataset": g.night_ds.mean(),
            "night_lift_archive": g.night_hr.mean() / h_night if h_night else np.nan,
            "rain_agreement": (g.rain_hr == g.rain_ds).mean(),
            "rain_precision": rain_precision, "rain_recall": rain_recall,
            "rain_kappa": rain_kappa,
            "night_agreement": (g.night_hr == g.night_ds).mean(),
            "night_precision": night_precision, "night_recall": night_recall,
            "night_kappa": night_kappa,
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--weather", default="data/external/weather_houston_hourly.csv")
    ap.add_argument("--state", default="TX"); ap.add_argument("--city", default="Houston")
    ap.add_argument("--rain-mm", type=float, default=config.RAIN_MM,
                    help="hour counts as rainy at or above this (mm)")
    ap.add_argument("--sweep", help="comma list of thresholds, e.g. 0.1,0.3,0.5,1,2")
    ap.add_argument("--out", default="data/external/exposure_baseline.csv")
    a = ap.parse_args()

    w = pd.read_csv(a.weather, parse_dates=["time"])
    c = load_crashes(a, w)
    lo, hi = c.hour.min().floor("D"), c.hour.max()
    w = w[(w.time >= lo) & (w.time <= hi)]
    print(f"crashes {len(c):,}; period {lo.date()} .. {hi.date()}; weather hours used {len(w):,}")

    fmt = {"display.width": 240, "display.max_columns": 30, "display.float_format": "{:.3f}".format}
    if a.sweep:
        rows = []
        for t in [float(x) for x in a.sweep.split(",")]:
            r = table(w, c, t).query("year == 'all'").iloc[0]
            rows.append({"rain_mm": t, "hours_rain_share": r.hours_rain_share,
                         "crash_rain_share_archive": r.crash_rain_share_archive,
                         "crash_rain_share_dataset": r.crash_rain_share_dataset,
                         "rain_lift": r.rain_lift_archive, "rain_agreement": r.rain_agreement,
                         "rain_precision": r.rain_precision, "rain_recall": r.rain_recall,
                         "rain_kappa": r.rain_kappa})
        with pd.option_context(*[x for kv in fmt.items() for x in kv]):
            print(pd.DataFrame(rows).to_string(index=False))
        print("\nPrecision, recall and Cohen's kappa compare archive rain tags with the dataset flag.\n"
              "Agreement alone is misleading when the positive rates differ. Check rainy-hour plausibility\n"
              "against a station or a documented local climatology before adopting a threshold.")
        return 0

    out = table(w, c, a.rain_mm)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)
    with pd.option_context(*[x for kv in fmt.items() for x in kv]):
        print(out.to_string(index=False))
    print("\nRead it like this: a lift near 1 means crashes happen in that condition about as often as the\n"
          "condition occurs; well above 1 means more crashes per hour than usual. Traffic volume is not\n"
          "controlled for (fewer cars drive in heavy rain and at night), so call it 'historically more\n"
          "crashes per hour', never 'rain causes crashes'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
