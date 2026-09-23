"""Size the climatology-smoothing artefact by rebuilding the hazard under other modes.

diagnose_baseline.py section D shows the baseline level error is driven by where the
day-of-year climatological mean falls relative to each city's minimum-mortality
temperature: peak-minus-MMT tracks the modelled attributable fraction at Spearman +0.82.
That is a hypothesis about the hazard construction, and `t2m_baseline_mode` already
offers the alternatives needed to test it. This script runs that test without a cluster
job, by rebuilding the daily series from the raw UrbClim years and re-integrating the
impact functions over each mode.

Modes compared (the first three as named in the configs):
  climatology_mean  - day-of-year mean across the UrbClim years. The production default.
  warmest_summer    - the single year with the highest JJA domain mean.
  domain_peak_day   - the year containing the hottest single domain-mean day.
  per_year_mean     - integrate each observed year, then average. Not a config option;
                      added here because it is the correct functional (see below).

RESULT on rome/milan/sevilla/dublin, in baseline deaths per 100k against Masselot:

    city      production  climatology  per_year_mean  warmest_summer  Masselot
    dublin          0.02         0.00           0.71            3.27      0.53
    sevilla        23.54        15.55          24.07           33.91     13.44
    rome           27.51        33.93          47.09           77.60     21.26
    milan          32.76        28.46          46.15           70.79     17.06

Three conclusions, the last of which was not expected.

1. warmest_summer is NOT the fix. It overshoots everywhere - Dublin to 6.2x Masselot,
   Rome to 3.6x. It answers "how bad is a hot year", not "what does a typical year look
   like", which is what a baseline is for.

2. The defect is a functional-form error, not a missing tail. climatology_mean computes
   f(mean of the years); an average year's burden is mean of f(year). Exposure-response
   curves are convex, so the first is strictly smaller and the gap grows with
   interannual variance. Dublin's annual burden varies by a factor of 5,040 across ten
   years and its climatological peak falls BELOW its MMT, so its burden is identically
   zero. per_year_mean moves Dublin from 0.03x Masselot to 1.35x - essentially a fix.

3. But the same correction makes the hot cities WORSE: Rome 1.29x -> 2.22x, Milan
   1.92x -> 2.71x. So there are two independent faults that were partly cancelling, and
   the respectable-looking aggregate rank correlation was hiding both. Fault 2 - a
   roughly 2-3x hot-city inflation, presumably the pixelwise application or the domain
   scale - is now the larger open problem. Do not fix the hazard alone: doing so moves
   the hot cities further from every benchmark.

What is and is not being measured. This reproduces the *hazard*, not the full model: it
integrates the city-level impact functions over a reconstructed daily series, so it gives
the multiplicative effect of the mode on the baseline burden, not a new set of deaths.
That ratio is the quantity needed to decide whether re-running the pipeline is worth it.

Two reconstruction choices worth knowing:

  * The production hazard is applied pixelwise inside a city mask that is built in
    NB01-NB03 and is absent from the slimmed export. We work from the domain mean and
    add a per-city constant, calibrated so the climatology_mean series reproduces the
    model's own citymask mean for 2020. Levels therefore carry that approximation, but
    the mode-to-mode *ratio* - the output that matters here - is far less sensitive to it.
  * The model's 2020 adds a PROVIDE climate-change markup to the 2008-2017 climatology.
    The same additive offset is applied to every mode, so the comparison isolates the
    mode and not the markup.

Only cities with UrbClim netCDFs under data/<City>/UrbClim/UrbClimT2Mmean/ can be run.
Extracted daily domain means are cached to validation/cache/, so the slow pass over the
netCDFs happens once.

Usage:
    python validation/test_baseline_mode.py                  # every city with local data
    python validation/test_baseline_mode.py --cities rome milan
    python validation/test_baseline_mode.py --refresh        # re-read the netCDFs

Requires: xarray + netCDF4, i.e. the `urbanheat` conda env, not the base interpreter.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
URBAN_HEAT = HERE.parent
CACHE = HERE / "cache"
AUX = Path(
    r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_interim_for_figures"
)
AGES = ("<15", "15-64", "65+")


def find_local_cities() -> dict[str, str]:
    """Map slug -> on-disk city directory, for cities with UrbClim T2M present."""
    out = {}
    for d in sorted((URBAN_HEAT / "data").glob("*")):
        if not d.is_dir():
            continue
        if list(d.glob("UrbClim/UrbClimT2Mmean/T2M_year_daily_mean_*.nc")):
            out[d.name.lower()] = d.name
    return out


def domain_means(city_dir: str, slug: str, refresh: bool) -> pd.DataFrame:
    """Daily domain-mean T2M in degC, one column per UrbClim year."""
    cached = CACHE / f"urbclim_{slug}_domainmean.csv"
    if cached.exists() and not refresh:
        return pd.read_csv(cached, index_col=0)
    import xarray as xr

    files = sorted(
        glob.glob(str(URBAN_HEAT / "data" / city_dir
                      / "UrbClim/UrbClimT2Mmean/T2M_year_daily_mean_*.nc"))
    )
    cols = {}
    for f in files:
        m = re.search(r"(\d{4})\.nc$", f)
        if not m:
            continue
        year = int(m.group(1))
        with xr.open_dataset(f) as ds:
            v = ds.T2M.mean(dim=("x", "y")).values - 273.15
        # Drop the leap day so every year shares one day-of-year index.
        cols[year] = v[:365]
        print(f"    read {Path(f).name}")
    df = pd.DataFrame(cols)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(cached)
    return df


def build_modes(dm: pd.DataFrame) -> dict[str, np.ndarray]:
    """The three baseline-mode daily series, from the per-year domain means."""
    jja = dm.iloc[151:243]  # 1 Jun - 31 Aug on a 365-day index
    return {
        "climatology_mean": dm.mean(axis=1).values,
        "warmest_summer": dm[jja.mean(axis=0).idxmax()].values,
        "domain_peak_day": dm[dm.max(axis=0).idxmax()].values,
    }


def per_year_burdens(dm: pd.DataFrame, offset: float, ifs: dict,
                     w: dict[str, float]) -> pd.Series:
    """Burden computed separately for each observed year.

    This is the quantity the production default gets structurally wrong. An average
    year's burden is the MEAN OF THE ANNUAL BURDENS, mean_i f(T_i); climatology_mean
    instead computes the BURDEN OF THE MEAN YEAR, f(mean_i T_i). Exposure-response
    curves are convex, so by Jensen's inequality the second is always the smaller, and
    the gap widens with interannual variance. Averaging the years first therefore does
    not just add a tail back - it computes the right functional in the first place, and
    unlike warmest_summer it still estimates a typical year rather than an extreme one.
    """
    return pd.Series(
        {int(y): burden(dm[y].values + offset, ifs, w) for y in dm.columns}
    )


def load_if(slug: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    j = json.loads((URBAN_HEAT / "10288665" / f"masselot_if_curves_{slug}.json").read_text())
    rec = j["ifs_by_year"][str(j.get("reference_year", 2020))]
    return {a: (np.asarray(rec[a]["intensity"]), np.asarray(rec[a]["mdd"]))
            for a in AGES if a in rec}


def age_weights(slug: str) -> dict[str, float]:
    """Population shares by the model's age bands, from Masselot's city-age table."""
    geo = pd.read_csv(HERE / "reference_geographies.csv", dtype=str)
    urau = geo.loc[geo.slug == slug, "urau_code"].iloc[0]
    ca = pd.read_csv(URBAN_HEAT / "10288665" / "results" / "cityage.csv")
    ca = ca[ca.URAU_CODE == urau]
    old = ca[ca.agegroup.isin(["65-74", "75-84", "85+"])].agepop.sum()
    mid = ca[ca.agegroup.isin(["20-44", "45-64"])].agepop.sum()
    tot = old + mid
    # Masselot models no under-15 heat mortality, so that band contributes nothing.
    return {"<15": 0.0, "15-64": mid / tot, "65+": old / tot}


def burden(series: np.ndarray, ifs: dict, w: dict[str, float]) -> float:
    """Population-weighted annual burden per exposed person for one daily series."""
    return sum(
        w.get(a, 0.0) * np.interp(series, x, y).sum() for a, (x, y) in ifs.items()
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cities", nargs="*", default=None)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    local = find_local_cities()
    slugs = [s for s in (args.cities or local) if s in local]
    missing = sorted(set(args.cities or []) - set(local))
    if missing:
        print(f"[note] no local UrbClim for {missing}; "
              f"available: {sorted(local)}\n")
    if not slugs:
        print("[error] no cities with local UrbClim data to run")
        return 2

    mm = pd.read_csv(URBAN_HEAT / "10288665" / "results" / "city.csv")[
        ["URAU_CODE", "mmt"]
    ]
    geo = pd.read_csv(HERE / "reference_geographies.csv", dtype=str)

    rows = []
    for slug in slugs:
        print(f"[{slug}]")
        dm = domain_means(local[slug], slug, args.refresh)
        modes = build_modes(dm)

        # Calibrate to the production hazard: the offset that carries the raw domain-mean
        # climatology onto the model's own 2020 citymask series absorbs both the PROVIDE
        # markup and the core-vs-domain difference in one constant.
        h = pd.read_csv(AUX / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv")
        h = h[pd.to_datetime(h.date).dt.year == 2020]
        offset = h.mean_intensity_citymask_degC.mean() - modes["climatology_mean"].mean()

        ifs = load_if(slug)
        w = age_weights(slug)
        urau = geo.loc[geo.slug == slug, "urau_code"].iloc[0]
        mmt = float(mm.loc[mm.URAU_CODE == urau, "mmt"].iloc[0])

        base = burden(modes["climatology_mean"] + offset, ifs, w)
        py = per_year_burdens(dm, offset, ifs, w)
        for name in (*modes, "per_year_mean"):
            if name == "per_year_mean":
                b, adj = py.mean(), modes["climatology_mean"] + offset
            else:
                adj = modes[name] + offset
                b = burden(adj, ifs, w)
            rows.append({
                "slug": slug, "mode": name, "offset_c": offset,
                "peak_c": adj.max(), "mmt": mmt, "peak_above_mmt_c": adj.max() - mmt,
                "days_above_mmt": int((adj > mmt).sum()),
                "burden": b, "vs_climatology": b / base if base else np.nan,
            })
        print(f"  offset {offset:+.2f} C | annual burden varies x"
              f"{py.max() / py.min():.1f} across {len(py)} observed years")

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("\n[baseline mode comparison]")
    print(out[["slug", "mode", "peak_c", "mmt", "peak_above_mmt_c",
               "days_above_mmt", "vs_climatology"]]
          .to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

    # Does a correction move the model TOWARD the independent benchmark or away from it?
    # A mode that fixes the functional form but worsens agreement is not yet a fix.
    val = args.out / "baseline_validation.csv"
    if val.exists():
        v = pd.read_csv(val)[["slug", "model_over_mas"]]
        piv = out.pivot(index="slug", columns="mode", values="vs_climatology")
        rec = v[v.slug.isin(piv.index)].set_index("slug").join(piv)
        for m in ("warmest_summer", "per_year_mean"):
            if m in rec:
                rec[f"implied_{m}"] = rec.model_over_mas * rec[m]
        cols = ["model_over_mas"] + [c for c in rec.columns if c.startswith("implied_")]
        print("\n[reconciliation]  model/Masselot rate ratio, now and under each mode")
        print(rec[cols].to_string(float_format=lambda x: f"{x:,.2f}"))
        print("  1.00 is exact agreement. A mode only helps where it moves a city")
        print("  toward 1, so hot cities already above 1 and cool cities below it have")
        print("  to be read together before calling any mode a fix.")

    print("\n[reading] climatology_mean computes f(mean of the years), but an average")
    print("  year's burden is mean of f(year). Convex response curves make the first")
    print("  strictly smaller, so the default under-states EVERY city, not only cool")
    print("  ones - which is why it cannot by itself explain the hot-city inflation.")

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / "baseline_mode_test.csv"
    out.to_csv(dest, index=False)
    print(f"\n[write] {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
