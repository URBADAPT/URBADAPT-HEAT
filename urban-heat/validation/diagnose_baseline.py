"""Localise why the modelled baseline's levels are wrong, city by city.

compare_baseline.py establishes that the cross-city *gradient* is sound (Spearman ~0.85
against Masselot) but the *levels* are graded in temperature: cool cities far too low,
hot cities too high. This script localises why.

THE HEADLINE RESULT, established by section D below: cool and hot cities are not two
faults but one. The baseline hazard is a day-of-year climatological mean, which collapses
the temperature distribution onto its central tendency and removes the hot tail entirely.
What each city's burden then depends on is simply where that compressed distribution
falls relative to its minimum-mortality temperature. Peak citymask temperature minus MMT
tracks the modelled attributable fraction at Spearman +0.82 across the 40 cities. Dublin
peaks 0.01 C above its MMT and Nantes 0.11 C above, so their burden collapses to nothing;
Rome and Athens sit 5-7 C above theirs for a hundred days with no compensating variance,
so theirs inflates. Fixing the hazard construction addresses both ends at once - which is
why the t2m_baseline_mode alternatives (warmest_summer, domain_peak_day) are the test to
run before any manuscript edit.

Sections A-C localise the remaining, city-specific problems on top of that.

  A. AGE INVERSION.
     Heat mortality is overwhelmingly a 65+ burden; Masselot's own city-age table puts
     the 65+ share around 0.8 in most cities. Our baseline hazard is a day-of-year
     climatological mean (climatology_mean over UrbClim 2008-2017), which averages away
     the hot tail. Where the 65+ exposure-response curve stays flat until a high
     threshold, the climatology never reaches the steep part and the dominant age group
     contributes almost nothing. Nantes is the clearest case: 1.69 of its 1.72 modelled
     2020 deaths sit in 15-64 and only 0.12 in 65+.
     Test: modelled 65+ share vs Masselot's 65+ share, per city.

     NOTE, against the obvious hypothesis: this is NOT the general explanation for the
     cool-city under-prediction. On the n128 run the rank correlation between the 65+
     share and the model/Masselot rate ratio is only +0.16. The collapse is severe but
     confined to five north-west maritime cities (Nantes, Amsterdam, Cologne, Hamburg,
     Rotterdam), while other badly under-predicted cities - Dublin at 0.03x, Stockholm
     at 0.14x - carry an entirely normal age structure. Those are a magnitude problem,
     not an age-structure problem, and need a separate explanation.

  B. DOMAIN COVERAGE.
     We apply the exposure-response pixelwise while Masselot applies it to a city mean,
     so we over-count where intra-urban spread is wide - worst where the modelled domain
     is the dense core with no cooler periphery to dilute the population-weighted
     exposure. Coverage tracks the rate ratio at Spearman -0.53.
     Caveat on reading it: model_pop/URAU_pop measures a URAU inconsistency as much as
     ours. The model appears to run at core-municipality scale throughout (Athens = the
     7 municipal communities, 665k; Milan = the 9 municipi, 1.38M), whereas URAU is the
     core city for some cities and the whole agglomeration for others (Paris URAU is
     6.87M against a modelled 2.32M). That also sits awkwardly with the manuscript's
     "all analyses are performed at the Functional Urban Area (FUA) scale" - worth
     settling with the model author, since it is not resolvable from the slimmed export.

     ATHENS IS STILL UNEXPLAINED and is the one city that should block a manuscript
     edit. Its modelled attributable fraction is 16.2% of all deaths, against a Masselot
     range of 0.07-2.08% and a European literature ceiling near 3-4%: arithmetically
     impossible, not merely biased. Ruled out so far - the coefficients (Athens' impact
     function is *lower* than Rome's at every temperature, and its MMT of 23.98 C is the
     highest in the sample), hazard bookkeeping (366 days, frequencies summing to 1.0),
     social vulnerability (SVI 0.477 against Rome's 0.499), and the section-D mechanism
     alone (at a comparable 5.7 C peak exceedance, Ljubljana/Rome/Milan land near 3%,
     so Athens is ~5x above even that trend). What remains is the exposure side - the
     age grid or city_mask - which lives in NB01-NB03 and is absent from the export.

Age splits live in the interim companion tree (annual_heat_deaths_generic_<city>.csv),
not in the slimmed results export - see --aux-base.

Usage:
    python validation/diagnose_baseline.py
    python validation/diagnose_baseline.py --outputs-base ... --aux-base ...

Requires: pandas, numpy. Reads reference_geographies.csv for the URAU mapping.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
URBAN_HEAT = HERE.parent
JUNO = Path(r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull")
DEFAULT_OUTPUTS = JUNO / "n128_results"
DEFAULT_AUX = JUNO / "n128_interim_for_figures"

# Below this, the modelled 65+ share is not credible for a heat burden.
AGE_SHARE_FLOOR = 0.50
# Exposure population this far short of the URAU means the domain is the core, not the city.
COVERAGE_FLOOR = 0.60
# Above this share of all-cause deaths, a heat burden is not physically credible:
# Masselot spans 0.07-2.08% across these cities and the literature tops out near 3-4%.
AF_IMPLAUSIBLE = 3.0


def masselot_age_share() -> pd.DataFrame:
    """Share of Masselot heat deaths falling in 65+ per URAU."""
    d = pd.read_csv(URBAN_HEAT / "10288665" / "results" / "cityage.csv")
    d["is_65p"] = d.agegroup.isin(["65-74", "75-84", "85+"])
    tot = d.groupby("URAU_CODE").excess_heat_est.sum()
    old = d[d.is_65p].groupby("URAU_CODE").excess_heat_est.sum()
    return pd.DataFrame({"mas_share_65p": (old / tot)}).reset_index().rename(
        columns={"URAU_CODE": "urau_code"}
    )


def model_age_split(aux: Path, slug: str) -> dict | None:
    """Modelled 2020 deaths by age band from the interim companion tree."""
    path = aux / slug / "interim" / f"annual_heat_deaths_generic_{slug}.csv"
    if not path.exists():
        return None
    d = pd.read_csv(path)
    row = d.loc[d.year == 2020]
    if row.empty:
        return None
    row = row.iloc[0]
    bands = {c: float(row[c]) for c in ("<15", "15-64", "65+") if c in d.columns}
    total = sum(bands.values())
    return {
        "model_deaths_65p": bands.get("65+", np.nan),
        "model_deaths_15_64": bands.get("15-64", np.nan),
        "model_share_65p": (bands.get("65+", np.nan) / total) if total > 0 else np.nan,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outputs-base", type=Path, default=DEFAULT_OUTPUTS)
    ap.add_argument("--aux-base", type=Path, default=DEFAULT_AUX)
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    val = args.out / "baseline_validation.csv"
    if not val.exists():
        print(f"[error] {val} missing - run compare_baseline.py first")
        return 2
    df = pd.read_csv(val)

    splits = []
    for slug in df.slug:
        s = model_age_split(args.aux_base, slug)
        splits.append({"slug": slug, **(s or {})})
    df = df.merge(pd.DataFrame(splits), on="slug", how="left")
    df = df.merge(masselot_age_share(), on="urau_code", how="left")
    df["age_share_gap"] = df.model_share_65p - df.mas_share_65p

    missing = int(df.model_share_65p.isna().sum())
    if missing:
        print(f"[note] no age split for {missing} cities - check --aux-base "
              f"({args.aux_base})\n")

    pd.set_option("display.width", 200)

    print("[A] age inversion - modelled 65+ share of heat deaths vs Masselot's")
    a = df[df.model_share_65p.notna()].sort_values("model_share_65p")
    cols = ["slug", "model_deaths", "model_deaths_65p", "model_deaths_15_64",
            "model_share_65p", "mas_share_65p", "age_share_gap", "model_over_mas"]
    print(a[cols].head(12).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    flagged = a[a.model_share_65p < AGE_SHARE_FLOOR]
    print(f"\n  {len(flagged)}/{len(a)} cities below a {AGE_SHARE_FLOOR:.0%} 65+ share: "
          f"{', '.join(flagged.slug) or 'none'}")
    if len(a) > 2:
        r = a.model_share_65p.corr(a.model_over_mas, method="spearman")
        print(f"  Spearman(65+ share, model/Masselot rate ratio) = {r:+.2f}")
        print("  A weak value means age inversion is a distinct fault affecting a few"
              " cities,\n  not the general cause of under-prediction - check the flagged"
              " list against\n  the worst rate ratios before attributing them to the"
              " same mechanism.")

    print("\n[B] domain coverage - exposure population as a share of the URAU population")
    b = df.sort_values("model_pop_over_urau_pop")
    cols = ["slug", "model_pop", "mas_pop", "model_pop_over_urau_pop",
            "model_rate", "mas_rate", "model_over_mas"]
    print(b[cols].head(10).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    low = b[b.model_pop_over_urau_pop < COVERAGE_FLOOR]
    print(f"\n  {len(low)}/{len(b)} cities below {COVERAGE_FLOOR:.0%} coverage: "
          f"{', '.join(low.slug) or 'none'}")
    ok = b[np.isfinite(b.model_pop_over_urau_pop) & np.isfinite(b.model_over_mas)]
    if len(ok) > 2:
        r = ok.model_pop_over_urau_pop.corr(ok.model_over_mas, method="spearman")
        print(f"  Spearman(coverage, model/Masselot rate ratio) = {r:+.2f}"
              "  - a negative value means partial domains inflate the rate.")

    # Cities that are extreme on the rate ratio beyond what the coverage trend explains
    # are likelier bugs than biases, and should be fixed rather than caveated.
    res = ok.copy()
    if len(res) > 3:
        x = np.log(res.model_pop_over_urau_pop.clip(lower=1e-3))
        y = np.log(res.model_over_mas.clip(lower=1e-3))
        fit = np.polyfit(x, y, 1)
        res["resid"] = y - np.polyval(fit, x)
        out = res.reindex(res.resid.abs().sort_values(ascending=False).index)
        print("\n  largest residuals against that trend (candidate bugs, not biases):")
        print(out[["slug", "model_pop_over_urau_pop", "model_over_mas", "resid"]]
              .head(5).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

    # ---- C. attributable-fraction plausibility -------------------------------------
    # The one screen that does not depend on getting the exposure population right:
    # heat deaths as a share of all-cause deaths in the same population. Masselot spans
    # 0.07-2.08% across the 40 cities and the European literature tops out near 3-4%, so
    # anything far above that is arithmetically impossible rather than merely biased.
    meta = pd.read_csv(URBAN_HEAT / "10288665" / "metadata.csv")[
        ["URAU_CODE", "deathrate_tot"]
    ].rename(columns={"URAU_CODE": "urau_code"})
    mas_af = pd.read_csv(URBAN_HEAT / "10288665" / "results" / "city.csv")[
        ["URAU_CODE", "af_heat_est", "mmt"]
    ].rename(columns={"URAU_CODE": "urau_code"})
    df = df.merge(meta, on="urau_code", how="left").merge(mas_af, on="urau_code", how="left")
    df["model_af_pct"] = df.model_deaths / (df.model_pop * df.deathrate_tot) * 100

    print("\n[C] attributable fraction - modelled heat deaths as % of all-cause deaths")
    c = df.sort_values("model_af_pct", ascending=False)
    print(c[["slug", "model_deaths", "model_af_pct", "af_heat_est", "model_over_mas"]]
          .head(8).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"\n  Masselot AF across these cities: {df.af_heat_est.min():.2f}"
          f" - {df.af_heat_est.max():.2f}%")
    over = c[c.model_af_pct > AF_IMPLAUSIBLE]
    print(f"  modelled AF above {AF_IMPLAUSIBLE}% (not physically credible): "
          f"{', '.join(over.slug) or 'none'}")

    # ---- D. the unifying mechanism ---------------------------------------------------
    rows = []
    for slug in df.slug:
        f = args.aux_base / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv"
        if not f.exists():
            continue
        h = pd.read_csv(f)
        h = h[pd.to_datetime(h.date).dt.year == 2020]
        rows.append({
            "slug": slug,
            "citymask_max_c": h.mean_intensity_citymask_degC.max(),
            "domain_offset_c": (h.mean_intensity_citymask_degC.mean()
                                - h.mean_intensity_degC.mean()),
        })
    if rows:
        df = df.merge(pd.DataFrame(rows), on="slug", how="left")
        df["peak_above_mmt_c"] = df.citymask_max_c - df.mmt
        print("\n[D] where the climatology falls relative to each city's MMT")
        v = df[df.peak_above_mmt_c.notna()]
        print(f"  Spearman(peak above MMT, modelled AF)          "
              f"= {v.peak_above_mmt_c.corr(v.model_af_pct, method='spearman'):+.2f}")
        print(f"  Spearman(peak above MMT, model/Masselot ratio) "
              f"= {v.peak_above_mmt_c.corr(v.model_over_mas, method='spearman'):+.2f}")
        print(f"  Spearman(core-vs-domain offset, modelled AF)   "
              f"= {v.domain_offset_c.corr(v.model_af_pct, method='spearman'):+.2f}")
        ends = pd.concat([v.nsmallest(4, "peak_above_mmt_c"), v.nlargest(4, "peak_above_mmt_c")])
        print()
        print(ends[["slug", "citymask_max_c", "mmt", "peak_above_mmt_c",
                    "model_af_pct", "model_over_mas"]]
              .to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
        print("\n  A climatological-mean hazard has no tail, so a city whose peak barely")
        print("  reaches its MMT records almost no burden while one sitting well above it")
        print("  records a large one. This is the single mechanism behind both ends of the")
        print("  temperature-graded error - test it with t2m_baseline_mode=warmest_summer.")

    dest = args.out / "baseline_diagnostics.csv"
    df.to_csv(dest, index=False)
    print(f"\n[write] {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
