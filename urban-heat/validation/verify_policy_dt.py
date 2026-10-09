"""Do the policy-induced temperature changes survive the ERA5 correction unchanged?

A co-author's condition for adopting the correction. The naive expectation - mine
included - is yes: the correction is a per-day additive shift of the whole field, policy
dT is also additive, so they should compose without interacting. The heterogeneity check
supports half of that (pixel anomalies identical to 6e-06 degC).

But the AC waste-heat term is NOT a fixed dT. It is produced by an activation model
(`kou_cdd_share`, with t_on_c / t_full_c thresholds in each city config) driven by
temperature, so lowering the hazard lowers AC activation and therefore lowers the waste
heat it emits. That is a real physical coupling, not a leak in the correction - but it
means the condition as literally stated does not hold, and the size of the effect needs
reporting rather than asserting.

Tree cooling is bridged from a GVI-to-LST coefficient and should be temperature-
independent, so it is the control: if tree_cooling_C moves, something IS leaking.

Compares <city>_hazard_modification_comparison.csv between the two runs.

Usage:
    python validation/verify_policy_dt.py
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

COR = r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40"
PUB = r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results"


def main() -> int:
    rows = []
    for d in sorted(glob.glob(os.path.join(COR, "*", "tables", "*_hazard_modification_comparison.csv"))):
        city = os.path.basename(d).replace("_hazard_modification_comparison.csv", "")
        p = os.path.join(PUB, city, "tables", f"{city}_hazard_modification_comparison.csv")
        if not os.path.exists(p):
            continue
        c, b = pd.read_csv(d), pd.read_csv(p)
        if "year" not in c or "year" not in b:
            continue
        m = b.merge(c, on="year", suffixes=("_pub", "_cor"))
        r = {"city": city, "n_years": len(m)}
        for col in ("tree_cooling_C", "ac_warming_C"):
            a, z = f"{col}_pub", f"{col}_cor"
            if a not in m or z not in m:
                continue
            ok = m[a].notna() & m[z].notna()
            if not ok.any():
                r[f"{col}_pub"] = np.nan
                r[f"{col}_cor"] = np.nan
                r[f"{col}_rel"] = np.nan
                continue
            pv, cv = m.loc[ok, a].mean(), m.loc[ok, z].mean()
            r[f"{col}_pub"] = pv
            r[f"{col}_cor"] = cv
            r[f"{col}_rel"] = (cv / pv) if pv else np.nan
        rows.append(r)

    t = pd.DataFrame(rows)
    if t.empty:
        print("[error] no comparable cities")
        return 2
    print(f"cities compared: {len(t)}\n")

    for col, verdict in (("tree_cooling_C", "CONTROL - should be unchanged"),
                         ("ac_warming_C", "temperature-driven - expected to move")):
        pc, cc, rc = f"{col}_pub", f"{col}_cor", f"{col}_rel"
        if rc not in t:
            continue
        v = t[t[rc].notna()]
        print(f"=== {col}  ({verdict}) ===")
        if v.empty:
            print("  no city has this populated "
                  "(tree_cooling_C is NaN wherever the LCZ cooling coefficients are missing)\n")
            continue
        print(f"  cities with values : {len(v)} / {len(t)}")
        print(f"  mean published     : {v[pc].mean():+.4f} degC")
        print(f"  mean corrected     : {v[cc].mean():+.4f} degC")
        print(f"  ratio cor/pub      : median {v[rc].median():.3f}  "
              f"range {v[rc].min():.3f} to {v[rc].max():.3f}")
        unchanged = int((v[rc].sub(1).abs() < 0.01).sum())
        print(f"  within 1% of unchanged: {unchanged}/{len(v)}\n")

    v = t[t.get("ac_warming_C_rel").notna()] if "ac_warming_C_rel" in t else pd.DataFrame()
    if not v.empty:
        print("=== AC waste heat, most-changed cities ===")
        s = v.reindex(v.ac_warming_C_rel.sub(1).abs().sort_values(ascending=False).index)
        print(s[["city", "ac_warming_C_pub", "ac_warming_C_cor", "ac_warming_C_rel"]]
              .head(8).to_string(index=False, float_format=lambda x: f"{x:,.4f}"))

    out = "validation/outputs/policy_dt_check.csv"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t.to_csv(out, index=False)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
