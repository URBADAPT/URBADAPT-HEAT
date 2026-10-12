"""Test whether the baseline level bias propagates into the quantities the paper reports.

compare_baseline.py establishes that the modelled baseline is right in rank (Spearman
~0.85 vs Masselot) but wrong in level, with the error graded in temperature. That is only
a problem for the manuscript to the extent it reaches the reported outcomes. This script
measures that directly instead of arguing it.

The reasoning being tested. A pathway's percentage mortality reduction is a ratio of two
quantities that share the baseline, so a common multiplicative bias should cancel out of
it. Absolute outcomes - avoided deaths per 100k, cost per death avoided - do not share
that protection: the bias lands in the denominator of cost-effectiveness essentially
undiluted. And because the bias is graded in temperature rather than constant, nothing
cancels out of *cross-city* comparisons, which is the manuscript's main axis.

So for each pathway we correlate the per-city bias ratio against:
  pct_reduction        - expected to be INSENSITIVE if the ratio argument holds
  deaths_per_100k_year - expected to inherit the bias
  cost_per_death       - expected to inherit its inverse

A large |rho| for pct_reduction would break the ratio argument and with it the defence
that relative claims survive. A large |rho| for cost_per_death is expected and bounds
which cost-effectiveness statements can be made city by city.

Pathway rows are matched on mechanism keywords, never on exact labels: the label text
varies by city (see the EWS naming trap), so exact matching silently drops cities.

Usage:
    python validation/propagate_bias.py
    python validation/propagate_bias.py --outputs-base "G:/.../n128_results"

Requires: pandas, numpy. Run compare_baseline.py first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUTS = Path(
    r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results"
)


def classify(label: str) -> str | None:
    """Map a city-specific policy label onto a pathway."""
    s = str(label).lower()
    if "tree" in s:
        # Several tree variants (uniform / equity / O&M multiples) exist per city; the
        # uniform allocation at base maintenance is the headline one.
        return "trees" if "uniform" in s or "base" in s else None
    if "ac" in s:
        return "ac_net" if "net" in s else None
    if "ews" in s:
        return "ews"
    return None


def read_outcomes(base: Path, slug: str) -> pd.DataFrame:
    """Per-pathway outcomes for one city, long format."""
    eff = base / slug / "tables" / f"{slug}_policy_effectiveness.csv"
    cea = base / slug / "tables" / f"{slug}_cea_summary.csv"
    rows = []
    if eff.exists():
        d = pd.read_csv(eff)
        d["pathway"] = d.policy.map(classify)
        for _, r in d[d.pathway.notna()].iterrows():
            rows.append({
                "slug": slug,
                "pathway": r.pathway,
                "pct_reduction": abs(float(r.pct_reduction)),
                "deaths_per_100k_year": abs(float(r.deaths_per_100k_year)),
            })
    if cea.exists():
        c = pd.read_csv(cea)
        c["pathway"] = c.Policy.map(classify)
        c = c[c.pathway.notna()]
        for _, r in c.iterrows():
            cost = float(r.Cost_per_Death_EUR)
            # Cities where a pathway avoids ~0 deaths produce ~1e10 EUR/death artefacts
            # (Madrid and Sevilla greening). They are censored downstream in the figures;
            # keeping them here would dominate any correlation.
            if not np.isfinite(cost) or cost > 5e7:
                cost = np.nan
            for row in rows:
                if row["slug"] == slug and row["pathway"] == r.pathway:
                    row["cost_per_death"] = cost
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outputs-base", type=Path, default=DEFAULT_OUTPUTS)
    ap.add_argument("--out", type=Path, default=HERE / "outputs")
    args = ap.parse_args()

    val = args.out / "baseline_validation.csv"
    if not val.exists():
        print(f"[error] {val} missing - run compare_baseline.py first")
        return 2
    bias = pd.read_csv(val)[["slug", "model_over_mas", "model_over_ref", "model_rate"]]

    frames = [read_outcomes(args.outputs_base, s) for s in bias.slug]
    out = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    out = out.merge(bias, on="slug", how="left")
    out["log_bias"] = np.log(out.model_over_mas.where(out.model_over_mas > 0))

    pd.set_option("display.width", 200)
    metrics = ["pct_reduction", "deaths_per_100k_year", "cost_per_death"]
    print("[propagation]  Spearman(bias ratio vs Masselot, outcome), per pathway\n")
    hdr = f"{'pathway':10s} {'n':>4s}" + "".join(f"{m:>24s}" for m in metrics)
    print(hdr)
    print("-" * len(hdr))
    for pw, g in out.groupby("pathway"):
        cells = ""
        for m in metrics:
            sub = g[np.isfinite(g.log_bias) & g[m].notna()]
            if len(sub) < 5:
                cells += f"{'n/a':>24s}"
                continue
            rho = sub.log_bias.corr(sub[m], method="spearman")
            cells += f"{f'{rho:+.2f}  (n={len(sub)})':>24s}"
        print(f"{pw:10s} {len(g):>4d}{cells}")

    print("\n[reading]")
    print("  pct_reduction        near 0 => the ratio cancels the bias; relative")
    print("                       effectiveness claims survive the level error.")
    print("  deaths_per_100k_year strong +ve => absolute burden claims inherit the bias.")
    print("  cost_per_death       strong -ve => cost-effectiveness inherits its inverse;")
    print("                       per-city EUR/death figures are not defensible where the")
    print("                       baseline is.")

    # The raw correlations above are CONFOUNDED and must not be read as causal. The bias
    # is graded in temperature, and the true effectiveness of every pathway is plausibly
    # graded in temperature too, so bias and outcome would correlate even if the bias had
    # no effect on the outcome at all. The discriminating test is whether the part of the
    # bias that temperature does NOT explain still tracks the outcome.
    meta = pd.read_csv(HERE.parent / "10288665" / "metadata.csv")[["LABEL", "tmean"]]
    geo = pd.read_csv(HERE / "reference_geographies.csv", dtype=str)[["slug", "urau_code"]]
    mas = pd.read_csv(HERE.parent / "10288665" / "results" / "city.csv")[
        ["URAU_CODE", "LABEL"]
    ]
    tmap = (
        geo.merge(mas.rename(columns={"URAU_CODE": "urau_code"}), on="urau_code")
        .merge(meta, on="LABEL")[["slug", "tmean"]]
    )
    out = out.merge(tmap, on="slug", how="left")

    print("\n[deconfounded]  bias residual after removing its temperature trend")
    print("  If the raw correlation is driven by climate rather than by the bias, the")
    print("  residual correlation collapses toward 0 and relative claims are safe.\n")
    hdr2 = (f"{'pathway':10s} {'metric':22s} {'n':>4s}  {'raw rho':>9s}  "
            f"{'resid rho':>9s}  {'rho(T, outcome)':>16s}")
    print(hdr2)
    print("-" * len(hdr2))
    for pw, g in out.groupby("pathway"):
        for m in metrics:
            sub = g[np.isfinite(g.log_bias) & g[m].notna() & g.tmean.notna()]
            if len(sub) < 8:
                continue
            fit = np.polyfit(sub.tmean, sub.log_bias, 1)
            resid = sub.log_bias - np.polyval(fit, sub.tmean)
            raw = sub.log_bias.corr(sub[m], method="spearman")
            res = resid.corr(sub[m], method="spearman")
            rt = sub.tmean.corr(sub[m], method="spearman")
            print(f"  {pw:10s} {m:22s} {len(sub):>4d}  {raw:>+9.2f}  "
                  f"{res:>+9.2f}  {rt:>+16.2f}")
        print()

    dest_note = (
        "  A residual rho that stays large is evidence the bias itself moves the\n"
        "  reported outcome. One that collapses means temperature drove both, and the\n"
        "  percentage-reduction claims are defensible despite the level error."
    )
    print(dest_note)

    dest = args.out / "bias_propagation.csv"
    out.to_csv(dest, index=False)
    print(f"\n[write] {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
