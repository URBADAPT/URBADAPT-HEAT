"""Compare the modelled 2020 heat-death baseline against Masselot and independent sources.

Answers the reviewer question "how close is the modelled baseline to real values" with
three nested comparisons, weakest to strongest:

  1. vs Masselot et al. 2023 (10288665/results/city.csv, excess_heat_est), all 40 cities.
     This is a REPRODUCTION check, not validation: we drive the model with Masselot's own
     exposure-response coefficients, so it asks whether our spatially-resolved, FUA-scale
     implementation recovers the source study's city totals - not whether either is right.

  2. vs Sante publique France departemental attributable deaths (MMT-referenced, so
     methodologically like-for-like). Genuinely independent of Masselot's coefficients.

  3. vs Spain MoMo provincial deaths attributable to temperature excess. Independent but
     threshold-referenced and therefore conservative; the mas_over_ref column reports
     Masselot against the same benchmark so the definitional offset can be read off
     separately from our own bias.

Two framing points the outputs are built around:

  * The modelled "2020" is a climatology (climatology_mean over UrbClim 2008-2017), not
     calendar-year 2020. Benchmarks are therefore multi-year means, never single years.
  * Benchmark geographies are NUTS-3 (departement / province) and mostly larger and more
     rural than the modelled FUA. Since hinterland is cooler, a benchmark rate is usually
     a LOWER bound on the city rate: model_over_ref slightly above 1 is the expected
     result, and below 1 is a red flag. reference_geographies.csv grades each match.

Rates are per 100k. The model denominator is its own exposure population (the sum of
`population` in <city>_policy_comparison.csv), not config pop_k, which is unusable for
four cities and off by >2x for Porto.

Usage:
    python validation/compare_baseline.py
    python validation/compare_baseline.py --outputs-base "G:/.../n128_results"
    python validation/compare_baseline.py --min-year 2014 --max-year 2022

Requires: pandas, numpy. Run validation/fetch_benchmarks.py first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
URBAN_HEAT = HERE.parent
MASSELOT_CITY = URBAN_HEAT / "10288665" / "results" / "city.csv"
DEFAULT_OUTPUTS = Path(
    r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results"
)


def read_model(base: Path, slugs: list[str]) -> pd.DataFrame:
    """Modelled 2020 baseline deaths and exposure population per city."""
    rows = []
    for slug in slugs:
        tables = base / slug / "tables"
        deaths_csv = tables / f"annual_heat_deaths_baseline_current_ac_{slug}.csv"
        pop_csv = tables / f"{slug}_policy_comparison.csv"
        if not deaths_csv.exists():
            print(f"  [skip]  {slug}: {deaths_csv.name} not found")
            continue
        d = pd.read_csv(deaths_csv)
        v = d.loc[d.year == 2020, "deaths_overall"]
        if v.empty:
            print(f"  [skip]  {slug}: no 2020 row")
            continue
        pop = pd.read_csv(pop_csv)["population"].sum() if pop_csv.exists() else np.nan
        rows.append({"slug": slug, "model_deaths": float(v.iloc[0]), "model_pop": pop})
    return pd.DataFrame(rows)


def read_masselot(urau: pd.Series) -> pd.DataFrame:
    m = pd.read_csv(MASSELOT_CITY).set_index("URAU_CODE")
    keep = [
        "excess_heat_est",
        "excess_heat_low",
        "excess_heat_hi",
        "rawrate_heat_est",
        "pop",
    ]
    out = m.loc[urau.values, keep].reset_index()
    return out.rename(
        columns={
            "URAU_CODE": "urau_code",
            "excess_heat_est": "mas_deaths",
            "excess_heat_low": "mas_lo",
            "excess_heat_hi": "mas_hi",
            "rawrate_heat_est": "mas_rate",
            "pop": "mas_pop",
        }
    )


def read_benchmarks(min_year: int, max_year: int) -> pd.DataFrame:
    """Multi-year mean of each independent series, restricted to complete years."""
    frames = []
    for src in ("spf", "momo"):
        path = HERE / "benchmarks" / f"{src}_annual.csv"
        if not path.exists():
            print(f"  [note]  {path.name} missing - run fetch_benchmarks.py --source {src}")
            continue
        d = pd.read_csv(path)
        d = d[d.year.between(min_year, max_year)]
        if d.empty:
            print(f"  [note]  {src}: no years in [{min_year}, {max_year}]")
            continue
        g = d.groupby("slug").agg(
            ref_deaths=("deaths", "mean"),
            ref_pop=("population", "first"),
            ref_n_years=("year", "size"),
            ref_first_year=("year", "min"),
            ref_last_year=("year", "max"),
        )
        g["ref_source"] = src
        frames.append(g.reset_index())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outputs-base", type=Path, default=DEFAULT_OUTPUTS)
    ap.add_argument("--min-year", type=int, default=2014)
    ap.add_argument("--max-year", type=int, default=2022)
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    geo = pd.read_csv(HERE / "reference_geographies.csv", dtype=str)
    print(f"[inputs] model   {args.outputs_base}")
    if not args.outputs_base.exists():
        print("  [error] outputs base does not exist - pass --outputs-base")
        return 2

    model = read_model(args.outputs_base, geo.slug.tolist())
    df = geo.merge(model, on="slug", how="inner")
    df = df.merge(read_masselot(df.urau_code), on="urau_code", how="left")
    bench = read_benchmarks(args.min_year, args.max_year)
    if not bench.empty:
        df = df.merge(bench.drop(columns="ref_source"), on="slug", how="left")
    else:
        for c in ("ref_deaths", "ref_pop", "ref_n_years", "ref_first_year", "ref_last_year"):
            df[c] = np.nan

    df["model_rate"] = df.model_deaths / df.model_pop * 1e5
    df["ref_rate"] = df.ref_deaths / df.ref_pop * 1e5
    df["model_over_mas"] = df.model_rate / df.mas_rate
    df["model_over_ref"] = df.model_rate / df.ref_rate
    # The definitional offset between Masselot and the national source. Reading
    # model_over_ref without this column conflates our bias with the two sources
    # measuring subtly different things.
    df["mas_over_ref"] = df.mas_rate / df.ref_rate
    df["in_masselot_ci"] = (df.model_deaths >= df.mas_lo) & (df.model_deaths <= df.mas_hi)
    df["model_pop_over_urau_pop"] = df.model_pop / df.mas_pop

    args.out.mkdir(parents=True, exist_ok=True)
    cols = [
        "slug", "urau_code", "source", "source_geo_name", "geo_match",
        "model_deaths", "model_pop", "model_rate",
        "mas_deaths", "mas_lo", "mas_hi", "mas_rate", "mas_pop",
        "ref_deaths", "ref_pop", "ref_rate", "ref_n_years", "ref_first_year", "ref_last_year",
        "model_over_mas", "model_over_ref", "mas_over_ref",
        "in_masselot_ci", "model_pop_over_urau_pop", "counterfactual", "notes",
    ]
    dest = args.out / "baseline_validation.csv"
    df[cols].sort_values("model_over_mas").to_csv(dest, index=False)

    # ---- console summary; every number here is quotable in the SI ----------------
    pd.set_option("display.width", 200)
    n = len(df)
    print(f"\n[vs Masselot]  n = {n} cities  (reproduction check, not validation)")
    print(f"  in Masselot 95% eCI     {int(df.in_masselot_ci.sum())}/{n}")
    within2 = ((df.model_over_mas > 0.5) & (df.model_over_mas < 2)).sum()
    print(f"  rate within factor 2    {int(within2)}/{n}")
    print(f"  Spearman (rates)        {df.model_rate.corr(df.mas_rate, method='spearman'):.3f}")
    print(f"  Spearman (totals)       {df.model_deaths.corr(df.mas_deaths, method='spearman'):.3f}")
    print(f"  median ratio            {df.model_over_mas.median():.2f}")

    # Is the error graded in climate? If so a common multiplicative bias does not cancel
    # out of cross-city comparisons, which is the paper's main axis.
    meta = pd.read_csv(URBAN_HEAT / "10288665" / "metadata.csv")[["URAU_CODE", "tmean"]]
    t = df.merge(meta.rename(columns={"URAU_CODE": "urau_code"}), on="urau_code")
    t = t[np.isfinite(t.tmean) & np.isfinite(t.model_over_mas) & (t.model_over_mas > 0)]
    lr = np.log(t.model_over_mas)
    slope = np.polyfit(t.tmean, lr, 1)[0]
    print(f"  log-ratio vs mean T     r = {lr.corr(t.tmean):+.2f}, "
          f"slope x{np.exp(slope):.2f} per degC  (n={len(t)})")

    ind = df[df.ref_rate.notna()].sort_values("model_over_ref")
    if not ind.empty:
        print(f"\n[vs independent sources]  n = {len(ind)} cities, "
              f"{args.min_year}-{args.max_year} means")
        show = ind[[
            "slug", "source", "geo_match", "model_rate", "ref_rate", "mas_rate",
            "model_over_ref", "mas_over_ref", "ref_n_years",
        ]]
        print(show.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
        print("\n  model_over_ref near or slightly above 1 is the expected result:")
        print("  benchmark geographies include cooler hinterland, so they under-state")
        print("  the city rate. Below 1 means the model is low against a lower bound.")

    print(f"\n[write] {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
