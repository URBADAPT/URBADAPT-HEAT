"""Check that the ERA5 reference correction leaves the future climate signal alone.

The correction quantile-maps each city's reference onto the ERA5-Land distribution. Two
properties have to hold for that to be a *bias* correction rather than a change to the
projection, and both are testable from the hazard event tables, which carry all four
target years:

  1. The shift must be IDENTICAL in every target year. It is estimated once, on the
     historical UrbClim record (2008-2017), and applied to the baseline calendar before
     the PROVIDE monthly deltas are added. If it differed by year, the mapping would be
     re-fitting itself against a warming climate and absorbing part of the signal.

  2. The future climate deltas must be preserved exactly. Because the shift is applied to
     the baseline calendar and the deltas are added afterwards, T'(y) = QM(T_clim) + d(y),
     so d(y) = T'(y) - T'(2020) is unchanged. Any departure means the correction is eating
     or inflating the warming signal.

The same argument covers policy-induced temperature changes (tree cooling, AC waste heat):
those are applied downstream in NB05/NB07 as additive dT on the hazard, and the correction
is a per-day additive constant on the whole field, so the two compose linearly and the
modelled policy effect in degC is untouched. What the correction does change is where on
the exposure-response curve that same dT lands - which is a real effect on the benefit,
not an artefact, and is why the trees pathway moves.

Usage:
    python validation/verify_delta_preservation.py
"""

from __future__ import annotations

import glob
import os
import sys

import pandas as pd

COR = r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40"
PUB = r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_interim_for_figures"
COL = "mean_intensity_citymask_degC"


def main() -> int:
    rows = []
    for d in sorted(glob.glob(os.path.join(COR, "*", "interim", "hazard_events_T2M_daily_*.csv"))):
        city = os.path.basename(d).replace("hazard_events_T2M_daily_", "").replace(".csv", "")
        p = os.path.join(PUB, city, "interim", f"hazard_events_T2M_daily_{city}.csv")
        if not os.path.exists(p):
            continue
        c, b = pd.read_csv(d), pd.read_csv(p)
        for f in (c, b):
            f["year"] = pd.to_datetime(f.date).dt.year
        cm, bm = c.groupby("year")[COL].mean(), b.groupby("year")[COL].mean()
        yrs = sorted(set(cm.index) & set(bm.index))
        if 2020 not in yrs:
            continue
        r = {"city": city}
        for y in yrs:
            r[f"shift_{y}"] = cm[y] - bm[y]
        for y in [x for x in yrs if x != 2020]:
            r[f"d{y}_pub"] = bm[y] - bm[2020]
            r[f"d{y}_cor"] = cm[y] - cm[2020]
            r[f"d{y}_err"] = (cm[y] - cm[2020]) - (bm[y] - bm[2020])
        rows.append(r)

    t = pd.DataFrame(rows)
    if t.empty:
        print("[error] no comparable cities found")
        return 2
    sh = [c for c in t.columns if c.startswith("shift_")]
    er = [c for c in t.columns if c.endswith("_err")]
    print(f"cities compared: {len(t)}\n")

    print("=== 1. is the applied shift the same in every target year? ===")
    spread = t[sh].max(axis=1) - t[sh].min(axis=1)
    print(f"  worst across-year spread of the shift: {spread.max():.2e} degC")
    print("  mean shift by year: "
          + ", ".join(f"{c.split('_')[1]}={t[c].mean():+.2f}" for c in sh))
    print(f"  shift range across cities: {t[sh[0]].min():+.2f} to {t[sh[0]].max():+.2f} degC\n")

    print("=== 2. are the future climate deltas preserved? ===")
    for c in er:
        print(f"  {c:12s} max |err| {t[c].abs().max():.2e} degC   mean {t[c].mean():+.2e}")
    print()
    for y in (2030, 2040, 2050):
        kp, kc = f"d{y}_pub", f"d{y}_cor"
        if kp in t.columns:
            print(f"  {y}: mean delta published {t[kp].mean():+.3f} degC | "
                  f"corrected {t[kc].mean():+.3f} degC")

    out = "validation/outputs/delta_preservation.csv"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t.to_csv(out, index=False)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
