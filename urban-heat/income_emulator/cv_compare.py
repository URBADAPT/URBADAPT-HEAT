#!/usr/bin/env python3
"""Compare CV schemes x targets on the same cohort.

Schemes : leave_one_country_out (transfer to a country with no income) vs
          leave_one_city_out     (transfer to a new city in a known country).
Targets : log relative index (inc_rel_to_city_median) vs within-city percentile
          rank (inc_pct_within_city).

Reuses run.py's data prep (merge + filters) and pipeline.evaluate.cross_validate
so the numbers match the production pipeline exactly.

    python cv_compare.py --config config_urbadapt.yaml
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from pipeline import schema, evaluate as E

TARGETS = {
    "index": {"precomputed_column": "inc_rel_to_city_median", "log_ratio": True},
    "rank":  {"precomputed_column": "inc_pct_within_city",    "log_ratio": False},
}
SCHEMES = ["leave_one_country_out", "leave_one_city_out"]


def prep(cfg: dict) -> pd.DataFrame:
    """Replicate run.py: load covariates, merge BOTH target columns + country, filter."""
    c = cfg["columns"]
    df = pd.read_csv(cfg["io"]["subdivisions"])
    labels = pd.read_csv(cfg["io"]["income_labels"])
    keys = cfg["io"].get("merge_on", [c["city_id"], c["subdivision_id"]])
    label_cols = list(keys)
    for x in ["inc_rel_to_city_median", "inc_pct_within_city", c.get("group"), c["income"]]:
        if x and x in labels.columns and x not in label_cols:
            label_cols.append(x)
    df = df.merge(labels[label_cols].drop_duplicates(keys), on=keys, how="left")
    req = [col for col in cfg.get("filters", {}).get("require_non_null", []) if col in df.columns]
    if req:
        before = len(df)
        df = df[df[req].notna().all(axis=1)].copy()
        print(f"filter: kept {len(df)}/{before} units (require {req} non-null)")
    return df


def run(cfg: dict, df: pd.DataFrame, target: str, scheme: str):
    c = cfg["columns"]
    cfg["target"]["precomputed_column"] = TARGETS[target]["precomputed_column"]
    cfg["target"]["log_ratio"] = TARGETS[target]["log_ratio"]
    cfg["validation"]["scheme"] = scheme
    train, _, _ = schema.split_train_predict(df, cfg)
    per_fold, oof = E.cross_validate(train, cfg)
    # actual number of held-out folds (countries or cities), distinct from the
    # per-city reporting rows that cross_validate always produces.
    grp = c["city_id"] if scheme == "leave_one_city_out" else cfg["validation"]["group_column"]
    nfolds = train[grp].nunique()
    # primary metric: zone-weighted mean of per-(reported)-city Spearman
    zw = np.average(per_fold["spearman"], weights=per_fold["n_zones"])
    sm = per_fold["spearman"].mean()
    return zw, sm, per_fold, oof, nfolds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_urbadapt.yaml")
    cfg = yaml.safe_load(open(ap.parse_args().config))
    df = prep(cfg)
    print(f"cohort: {len(df)} units, {df[cfg['columns']['city_id']].nunique()} cities, "
          f"{df[cfg['columns']['group']].nunique()} countries\n")

    print(f"{'metric':28s}{'index':>10s}{'rank':>10s}")
    print("-" * 48)
    saved = {}
    for scheme in SCHEMES:
        res = {t: run(cfg, df, t, scheme) for t in TARGETS}
        nfolds = res["index"][4]
        print(f"[{scheme}]  ({nfolds} held-out folds, 29 cities reported)")
        print(f"{'  per-fold zone-wtd Spearman':28s}{res['index'][0]:>10.3f}{res['rank'][0]:>10.3f}")
        print(f"{'  per-fold simple-mean':28s}{res['index'][1]:>10.3f}{res['rank'][1]:>10.3f}")
        saved[scheme] = res

    # Save the per-fold tables for the leave-one-city-out runs as artifacts.
    for t, outdir in [("index", "results"), ("rank", "results_rank")]:
        pf = saved["leave_one_city_out"][t][2]
        p = Path(outdir) / "cv_report_leave_one_city_out.csv"
        pf.to_csv(p, index=False)
        print(f"\nwrote {p}  ({len(pf)} cities)")

    # Show per-city detail for the city-out x rank run.
    print("\nleave_one_city_out, rank target - per-city Spearman:")
    pf = saved["leave_one_city_out"]["rank"][2].sort_values("spearman", ascending=False)
    print(pf.to_string(index=False))


if __name__ == "__main__":
    main()
