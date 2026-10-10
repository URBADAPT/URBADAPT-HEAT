"""Does the convolution reproduce the exact multi-year calculation IN THE PIPELINE?

The convolution was validated against the exact per-year ensemble using a reconstruction
of the hazard (city-level daily means plus a constant offset), not the production
pipeline. That left the strongest of the co-author's conditions - "the exact multi-year
calculation is reproduced closely across a representative range of cities" - resting on
a stand-in rather than on the model.

With the variance-only arm now run for all 40 cities, the pipeline states the answer
directly: the ratio of variance-only to uncorrected baseline deaths IS the factor the
convolution applied. Comparing that against the factor the exact per-year ensemble
predicts, for the cities whose UrbClim daily means are still cached, tests the
approximation where it is actually used.

    predicted = mean_y f(T_y) / f(mean_y T_y)      exact ensemble vs climatology
    actual    = deaths(variance arm) / deaths(uncorrected arm)

Agreement means the convolution behaves in the pipeline as it did in the reconstruction.

Usage:
    python validation/verify_convolution_in_pipeline.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# ROOT first so `cityheat` resolves to this worktree's copy rather than any installed
# one, HERE so the sibling validation modules import.
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

JUNO = r"C:\Users\giaco\OneDrive - IIASA\Documents\juno"
ARM_NONE = r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results"
ARM_VAR = os.path.join(JUNO, "arm_variance")
CACHE = os.path.join(HERE, "cache")
if not os.path.isdir(CACHE):  # fall back to the main checkout's caches
    CACHE = r"C:\Users\giaco\Documents\Github\URBADAPT-HEAT\urban-heat\validation\cache"
# The hazard tree that supplies the climatology MUST be the uncorrected one, because
# both arms being compared here (none, variance) ran with T2M_ERA5_BIAS_CORRECT=0.
# Taking it from the corrected tree instead anchors the curve 0.2-2.5 C too cool
# depending on the city, and on a convex curve that alone moves the predicted variance
# factor by tens of percent - an artefact of the check, not a property of the pipeline.
AUX = r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_interim_for_figures"


def deaths_2020(base: str, city: str) -> float:
    p = os.path.join(base, city, "tables", f"annual_heat_deaths_baseline_current_ac_{city}.csv")
    if not os.path.exists(p):
        return float("nan")
    d = pd.read_csv(p)
    v = d.loc[d.year == 2020, "deaths_overall"]
    return float(v.iloc[0]) if len(v) else float("nan")


def main() -> int:
    from test_baseline_mode import load_if, age_weights, burden

    # Load hazard_reconciliation by path rather than as cityheat.hazard_reconciliation:
    # the package __init__ imports climada, which the plain interpreter does not have,
    # while this module is pure numpy/pandas and needs nothing else.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_hazrec", os.path.join(ROOT, "cityheat", "hazard_reconciliation.py"))
    hazrec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hazrec)
    daily_anomalies = hazrec.daily_anomalies
    variance_corrected_curve = hazrec.variance_corrected_curve

    cached = sorted(
        os.path.basename(p).replace("urbclim_", "").replace("_domainmean.csv", "")
        for p in __import__("glob").glob(os.path.join(CACHE, "urbclim_*_domainmean.csv"))
    )
    if not cached:
        print(f"[error] no UrbClim caches under {CACHE}")
        return 2

    meta = pd.read_csv(os.path.join(
        r"C:\Users\giaco\Documents\Github\URBADAPT-HEAT\natcities_visual_items\tables",
        "city_metadata.csv"))[["city", "climate_cluster"]]

    rows = []
    for city in cached:
        dm = pd.read_csv(os.path.join(CACHE, f"urbclim_{city}_domainmean.csv"), index_col=0)
        clim = dm.mean(axis=1).values
        hz = os.path.join(AUX, city, "interim", f"hazard_events_T2M_daily_{city}.csv")
        if not os.path.exists(hz):
            continue
        h = pd.read_csv(hz)
        h = h[pd.to_datetime(h.date).dt.year == 2020]
        off = h.mean_intensity_citymask_degC.mean() - clim.mean()

        ifs = load_if(city)
        w = age_weights(city)
        exact = np.mean([burden(dm[y].values + off, ifs, w) for y in dm.columns])
        naive = burden(clim + off, ifs, w)
        anom = daily_anomalies(dm)
        conv = {a: (x, variance_corrected_curve(x, y, anom)) for a, (x, y) in ifs.items()}
        convolved = burden(clim + off, conv, w)

        d_none, d_var = deaths_2020(ARM_NONE, city), deaths_2020(ARM_VAR, city)
        rows.append({
            "city": city,
            "predicted_exact": exact / naive if naive else np.nan,
            "predicted_convolved": convolved / naive if naive else np.nan,
            "actual_pipeline": d_var / d_none if d_none else np.nan,
        })

    t = pd.DataFrame(rows).merge(meta, on="city", how="left")
    t["conv_vs_exact"] = t.predicted_convolved / t.predicted_exact
    t["pipeline_vs_exact"] = t.actual_pipeline / t.predicted_exact
    pd.set_option("display.width", 200)
    print("Correction factor on the 2020 baseline burden "
          "(variance correction relative to uncorrected)\n")
    print(t[["city", "climate_cluster", "predicted_exact", "predicted_convolved",
             "actual_pipeline", "conv_vs_exact", "pipeline_vs_exact"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    for lbl, c in (("convolution vs exact ensemble (reconstruction)", "conv_vs_exact"),
                   ("PIPELINE vs exact ensemble", "pipeline_vs_exact")):
        v = t[c].dropna()
        if v.empty:
            continue
        e = (v - 1).abs()
        print(f"\n{lbl}: median error {e.median():.1%}, worst {e.max():.1%}  (n={len(v)})")

    missing = sorted(set(meta.climate_cluster.dropna()) - set(t.climate_cluster.dropna()))
    if missing:
        print(f"\n[gap] no cached city in climate class(es): {', '.join(missing)} - "
              "the check does not cover them")

    out = os.path.join(HERE, "outputs", "convolution_in_pipeline.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t.to_csv(out, index=False)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
