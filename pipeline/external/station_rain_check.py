#!/usr/bin/env python3
"""Compare the share of rainy hours at a Houston airport station (NOAA ASOS data via the
Iowa Environmental Mesonet) with the Open-Meteo archive, for the same years.

Run from the project root:
  python pipeline/external/station_rain_check.py

Station precipitation is a point gauge with 0.01 inch (0.254 mm) resolution, so it cannot tell
0.1 mm from 0.3 mm. The thresholds below are therefore gauge steps, with the nearest mm value shown.
"""
import argparse, io, sys, urllib.request
import pandas as pd

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
STEPS_IN = [0.01, 0.02, 0.04, 0.08]          # inches
MM = {s: round(s * 25.4, 2) for s in STEPS_IN}


def fetch(station, y1, y2):
    q = (f"?station={station}&data=tmpf&data=p01i&year1={y1}&month1=1&day1=1"
         f"&year2={y2}&month2=1&day2=1&tz=Etc/UTC&format=onlycomma&latlon=no&elev=no"
         "&missing=empty&trace=0.0001&direct=no")
    req = urllib.request.Request(IEM + q, headers={"User-Agent": "waymark-rain-check"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def station_shares(text):
    df = pd.read_csv(io.StringIO(text), comment="#")
    df.columns = [c.strip() for c in df.columns]
    df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
    df["p01i"] = pd.to_numeric(df["p01i"], errors="coerce")
    df = df.dropna(subset=["valid"])
    df["hour"] = df["valid"].dt.floor("h")
    # hours with at least one observation of any kind form the denominator;
    # the hour's precipitation is the largest one-hour value reported in it
    g = df.groupby("hour")["p01i"].max()
    n_hours = len(g)
    out = {"hours_with_obs": n_hours, "hours_with_precip_value": int(g.notna().sum())}
    for s in STEPS_IN:
        out[s] = float((g.fillna(0) >= s - 1e-9).sum()) / max(n_hours, 1)
    return out


def archive_shares(path, y1, y2):
    df = pd.read_csv(path)
    print("archive columns:", list(df.columns))
    tcol = next((c for c in df.columns if "time" in c.lower() or "date" in c.lower()), df.columns[0])
    pcol = next((c for c in df.columns if "precip" in c.lower()), None)
    if pcol is None:
        sys.exit("No column with 'precip' in its name; tell me the header and I will adjust.")
    df[tcol] = pd.to_datetime(df[tcol], errors="coerce")
    df = df[(df[tcol].dt.year >= y1) & (df[tcol].dt.year < y2)]
    p = pd.to_numeric(df[pcol], errors="coerce").dropna()
    print(f"archive: using '{pcol}', {len(p)} hours in {y1}-{y2 - 1}")
    return {s: float((p >= MM[s] - 1e-6).mean()) for s in STEPS_IN}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", default="HOU,IAH")
    ap.add_argument("--start-year", type=int, default=2017)
    ap.add_argument("--end-year", type=int, default=2023, help="exclusive")
    ap.add_argument("--archive", default="data/external/weather_houston_hourly.csv")
    ap.add_argument("--from-file", action="append", default=[],
                    help="STATION=path.csv to use a saved download instead of fetching")
    a = ap.parse_args()
    local = dict(x.split("=", 1) for x in a.from_file)

    res = {}
    for st in a.stations.split(","):
        text = open(local[st]).read() if st in local else fetch(st, a.start_year, a.end_year)
        res[st] = station_shares(text)
        r = res[st]
        print(f"{st}: {r['hours_with_obs']} hours with observations "
              f"(expected about {(a.end_year - a.start_year) * 8766}); "
              f"{r['hours_with_precip_value']} hours carry a precipitation value")
    arc = archive_shares(a.archive, a.start_year, a.end_year)

    rows = []
    for s in STEPS_IN:
        row = {"gauge_step_in": s, "approx_mm": MM[s]}
        for st in res:
            row[f"{st}_hours_share"] = round(res[st][s], 3)
        row["archive_share_at_that_mm"] = round(arc[s], 3)
        rows.append(row)
    print()
    print(pd.DataFrame(rows).to_string(index=False))
    print("\nRead it like this: the archive column at 0.51 mm is your 0.5 mm threshold.\n"
          "If it is within a few percentage points of the station columns at 0.02 in, the threshold is\n"
          "plausible. A gauge cannot resolve below 0.01 in (0.25 mm), so 0.1 mm cannot be checked here.")


if __name__ == "__main__":
    main()
