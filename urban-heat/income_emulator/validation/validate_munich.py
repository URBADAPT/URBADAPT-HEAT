"""Objective external validation for Munich (a city with NO training income).

Compares the emulator's predicted within-city percentile (p_inc, from a deploy run)
against published income per Stadtbezirk (validation/reference/munich_income_stadtbezirk_2020.csv).
The median is the headline comparison (robust; the mean is inflated by income millionaires).
Uses the canonical pop-weighted Spearman (pipeline.evaluate._spearman).

    cd income_emulator
    python validation/validate_munich.py --pred validation/results/deploy_munich.csv
"""
from __future__ import annotations
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline import evaluate as E   # noqa: E402

REF = os.path.join(os.path.dirname(__file__), "reference", "munich_income_stadtbezirk_2020.csv")
# OSM sometimes labels Stadtbezirk 8 (Schwanthalerhoehe) numerically.
SPECIAL_LABEL_TO_NUM = {"8.2": 8}


def _gnorm(s: str) -> str:
    """German-aware key: ue/oe/ae/ss transliteration, then strip separators/case."""
    s = str(s).lower().strip()
    for a, b in [("ü", "ue"), ("ö", "oe"), ("ä", "ae"), ("ß", "ss")]:
        s = s.replace(a, b)
    return s.replace(" ", "").replace("-", "").replace(".", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="validation/results/deploy_munich.csv")
    ap.add_argument("--ref", default=REF)
    a = ap.parse_args()

    ref = pd.read_csv(a.ref, comment="#")
    num_by_key = {_gnorm(n): int(num) for n, num in zip(ref.stadtbezirk, ref.stadtbezirk_num)}

    pred = pd.read_csv(a.pred, dtype={"subcity_code": str})
    pred["num"] = pred["subcity_code"].map(
        lambda s: SPECIAL_LABEL_TO_NUM.get(str(s)) or num_by_key.get(_gnorm(s)))
    miss = pred[pred["num"].isna()]["subcity_code"].tolist()
    if miss:
        print(f"[warn] {len(miss)} predicted units not mapped to a Stadtbezirk: {miss}")

    df = pred.dropna(subset=["num"]).merge(ref, left_on="num", right_on="stadtbezirk_num", how="inner")
    pop = pd.to_numeric(df.get("pop_zone", pd.Series(1.0, index=df.index)), errors="coerce").fillna(0.0).values
    print(f"Munich objective validation (n={len(df)} Stadtbezirke, Einkommensteuerstatistik 2020):")
    for label, col in [("MEDIAN", "median_income_eur"), ("MEAN", "mean_income_eur")]:
        rho_w = E._spearman(df[col].values, df["p_inc"].values, pop)
        rho_u = E._spearman(df[col].values, df["p_inc"].values, None)
        print(f"  Spearman(p_inc, observed {label}): pop-weighted={rho_w:.3f}  unweighted={rho_u:.3f}")


if __name__ == "__main__":
    main()
