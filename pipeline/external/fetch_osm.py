#!/usr/bin/env python3
"""
Step 3. Download OpenStreetMap features for the study area through the Overpass API.

Usage:
    python pipeline/external/fetch_osm.py --cells out/cell_scores.csv --contact you@example.com
    python pipeline/external/fetch_osm.py --cells out/cell_scores.csv --contact you@example.com --roads

The bounding box comes from your own cells (lat/lng columns). It is split into tiles so no single
request is too big. Every response is cached under data/external/osm/, and files that already exist
are skipped, so you can re-run after an interruption. Be polite: one request at a time, with a pause.
Data (c) OpenStreetMap contributors, ODbL. Keep that attribution in the app and the README.
"""
import argparse, json, sys, time
from pathlib import Path

import httpx
import pandas as pd

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

POINT_FEATURES = {
    "signals": '["highway"="traffic_signals"]',
    "crossings": '["highway"="crossing"]',
    "stops": '["highway"="stop"]',
    "give_way": '["highway"="give_way"]',
    "level_crossings": '["railway"="level_crossing"]',
    "street_lamps": '["highway"="street_lamp"]',
    "traffic_calming": '["traffic_calming"]',
    "bus_stops": '["highway"="bus_stop"]',
    "roundabouts": '["junction"="roundabout"]',
}
ROAD_CLASSES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|motorway_link|trunk_link|primary_link|secondary_link"


def tiles(bbox, n):
    s, w, nn, e = bbox
    out = []
    for i in range(n):
        for j in range(n):
            out.append((s + (nn - s) * i / n, w + (e - w) * j / n,
                        s + (nn - s) * (i + 1) / n, w + (e - w) * (j + 1) / n))
    return out


def run_query(client, query, contact):
    last = None
    for attempt in range(6):
        for url in ENDPOINTS:
            try:
                r = client.post(url, data={"data": query}, timeout=300,
                                headers={"User-Agent": f"WAYMARK-research/0.1 ({contact})"})
                if r.status_code in (429, 504):
                    last = f"{r.status_code} from {url}"; time.sleep(30 * (attempt + 1)); continue
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as e:
                last = e; time.sleep(10)
    raise RuntimeError(f"Overpass failed: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True, help="CSV with lat,lng columns (out/cell_scores.csv)")
    ap.add_argument("--contact", required=True, help="your email or project URL, sent in the User-Agent")
    ap.add_argument("--roads", action="store_true", help="download road geometry (bigger; run after points)")
    ap.add_argument("--tiles", type=int, default=3, help="split the box into N x N tiles")
    ap.add_argument("--pad", type=float, default=0.02)
    ap.add_argument("--outdir", default="data/external/osm")
    a = ap.parse_args()

    cells = pd.read_csv(a.cells, usecols=["lat", "lng"])
    bbox = (cells.lat.min() - a.pad, cells.lng.min() - a.pad, cells.lat.max() + a.pad, cells.lng.max() + a.pad)
    print("bbox (s,w,n,e):", [round(x, 4) for x in bbox])
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    jobs = []
    for ti, (s, w, n, e) in enumerate(tiles(bbox, a.tiles)):
        box = f"({s:.5f},{w:.5f},{n:.5f},{e:.5f})"
        if a.roads:
            q = f'[out:json][timeout:280];way["highway"~"^({ROAD_CLASSES})$"]{box};out geom tags;'
            jobs.append((f"roads_t{ti}.json", q))
        else:
            for name, tag in POINT_FEATURES.items():
                q = f"[out:json][timeout:280];nwr{tag}{box};out center tags;"
                jobs.append((f"{name}_t{ti}.json", q))

    with httpx.Client() as client:
        for i, (fname, q) in enumerate(jobs, 1):
            path = out / fname
            if path.exists():
                print(f"[{i}/{len(jobs)}] skip {fname}"); continue
            print(f"[{i}/{len(jobs)}] {fname} ...", flush=True)
            data = run_query(client, q, a.contact)
            path.write_text(json.dumps({"elements": data.get("elements", [])}))
            print(f"    {len(data.get('elements', []))} elements")
            time.sleep(3)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
