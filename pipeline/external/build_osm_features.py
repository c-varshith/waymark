#!/usr/bin/env python3
"""
Step 4. Turn the cached OSM files into one row of features per H3 cell.

Usage:
    python pipeline/external/build_osm_features.py --res 9

Writes data/external/osm_cell_features.csv with columns:
  osm_signals, osm_crossings, osm_stops, osm_give_way, osm_level_crossings, osm_street_lamps,
  osm_traffic_calming, osm_bus_stops, osm_roundabouts            (counts per cell)
  osm_road_km, osm_major_road_km                                  (road length per cell)
  osm_mean_maxspeed_mph, osm_maxspeed_known_km                    (length-weighted, where tagged)
  osm_lit_km, osm_lit_tagged_km                                   (where the 'lit' tag exists)
Missing tags stay missing; they are never silently turned into 'no'.
"""
import argparse, glob, json, math, re, sys
from collections import defaultdict
from pathlib import Path

import h3
import pandas as pd

POINT_NAMES = ["signals", "crossings", "stops", "give_way", "level_crossings",
               "street_lamps", "traffic_calming", "bus_stops", "roundabouts"]
MAJOR = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"}


def hav_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def parse_maxspeed_mph(v):
    """'35 mph' -> 35; '50' (OSM default unit is km/h) -> 31; 'none' / 'signals' -> None."""
    if not v:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kph)?", str(v).lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return n if unit == "mph" else n * 0.621371


def point_of(el):
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", type=int, default=9)
    ap.add_argument("--indir", default="data/external/osm")
    ap.add_argument("--out", default="data/external/osm_cell_features.csv")
    a = ap.parse_args()
    ind = Path(a.indir)

    table = defaultdict(lambda: defaultdict(float))     # cell -> feature -> value

    for name in POINT_NAMES:
        files = sorted(glob.glob(str(ind / f"{name}_t*.json")))
        if not files:
            print(f"warning: no files for {name}"); continue
        seen = set()
        for f in files:
            for el in json.loads(Path(f).read_text())["elements"]:
                key = (el.get("type"), el.get("id"))
                if key in seen: continue
                seen.add(key)
                p = point_of(el)
                if p: table[h3.latlng_to_cell(p[0], p[1], a.res)][f"osm_{name}"] += 1

    road_files = sorted(glob.glob(str(ind / "roads_t*.json")))
    if not road_files:
        print("note: no road files; run fetch_osm.py --roads for road length, speed and lighting")
    seen = set()
    for f in road_files:
        for el in json.loads(Path(f).read_text())["elements"]:
            if el.get("id") in seen or "geometry" not in el: continue
            seen.add(el["id"])
            tags = el.get("tags", {})
            speed = parse_maxspeed_mph(tags.get("maxspeed"))
            lit = tags.get("lit")
            geom = [(g["lat"], g["lon"]) for g in el["geometry"]]
            for p, q in zip(geom, geom[1:]):
                d = hav_m(p, q)
                if d <= 0: continue
                cell = h3.latlng_to_cell((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, a.res)
                row = table[cell]
                km = d / 1000.0
                row["osm_road_km"] += km
                if tags.get("highway") in MAJOR: row["osm_major_road_km"] += km
                if speed is not None:
                    row["osm_maxspeed_known_km"] += km
                    row["_speed_x_km"] += speed * km
                if lit in ("yes", "no", "24/7", "automatic"):
                    row["osm_lit_tagged_km"] += km
                    if lit != "no": row["osm_lit_km"] += km

    df = pd.DataFrame.from_dict({k: dict(v) for k, v in table.items()}, orient="index").fillna(0.0)
    if "_speed_x_km" in df:
        known = df["osm_maxspeed_known_km"].replace(0, float("nan"))
        df["osm_mean_maxspeed_mph"] = df["_speed_x_km"] / known     # stays NaN where no speed is tagged
        df = df.drop(columns="_speed_x_km")
    df.index.name = "cell"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    df.sort_index().to_csv(a.out)
    print(f"wrote {a.out}: {len(df):,} cells, columns: {list(df.columns)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
