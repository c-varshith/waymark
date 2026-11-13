#!/usr/bin/env python3
"""
Step 5. The keep-or-drop test. Retrain with and without the OSM features on the SAME split and
compare against your published numbers (AUC 0.876 overall, 0.660 on low-history cells).

Run from the project root so `blackspot_pipeline` imports:
    python pipeline/external/compare_features.py --csv US_Accidents_March23.csv

It does not touch backend/ml_artifacts or out/. It only prints a table.
"""
import argparse, sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

import blackspot_pipeline as bp


def fit_predict(Xtr, ytr, Xte, feats, seeds=(0, 1, 2)):
    ps, models = [], []
    for s in seeds:
        m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=20,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                               random_state=s, verbose=-1).fit(Xtr[feats], ytr)
        ps.append(m.predict_proba(Xte[feats])[:, 1]); models.append(m)
    return np.mean(ps, axis=0), models


def score(y, p, low):
    return roc_auc_score(y, p), roc_auc_score(y[low], p[low]), bp.prec_at(y, p, .05), bp.recall_at(y, p, .10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--osm", default="data/external/osm_cell_features.csv")
    ap.add_argument("--target", type=int, default=2022)
    a = ap.parse_args()

    df = bp.load(SimpleNamespace(csv=a.csv, state="TX", city="Houston", res=9))
    T = a.target
    Xtr, ytr = bp.build(df, T - 2, T - 1)
    Xte, yte = bp.build(df, T - 1, T)
    base = list(Xtr.columns)

    osm = pd.read_csv(a.osm, index_col="cell")
    osm_cols = list(osm.columns)
    for X in (Xtr, Xte):
        for c in osm_cols:
            X[c] = osm[c].reindex(X.index)
        # missing speed stays NaN (LightGBM handles it); missing counts/lengths mean 'none found' = 0
        for c in osm_cols:
            if c != "osm_mean_maxspeed_mph":
                X[c] = X[c].fillna(0.0)

    y = yte.values
    low = (Xte["n_acc"] <= 2).values
    rows = {}
    p0, _ = fit_predict(Xtr, ytr, Xte, base)
    rows["baseline (18 features)"] = score(y, p0, low)
    p1, models = fit_predict(Xtr, ytr, Xte, base + osm_cols)
    rows[f"+ OSM ({len(osm_cols)} features)"] = score(y, p1, low)

    out = pd.DataFrame(rows, index=["AUC all", "AUC low-history", "precision top 5%", "capture top 10%"]).T
    with pd.option_context("display.float_format", "{:.4f}".format, "display.width", 160):
        print(out.to_string())
    print("\nReference from out/metrics.json: AUC all 0.8763, AUC low-history 0.6597.")

    gain = pd.Series(np.mean([m.booster_.feature_importance("gain") for m in models], axis=0),
                     index=base + osm_cols).sort_values(ascending=False)
    print("\nTop features by gain (share of total):")
    print((gain / gain.sum()).head(12).round(3).to_string())
    print("\nKeep a feature group only if low-history or overall AUC improves by more than the seed-to-seed\n"
          "noise (rerun with different seeds to check), or if it gives planners a clear new reason to show.\n"
          "Note: OSM is a current snapshot while labels are from 2021-2022, so newer infrastructure can leak in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
