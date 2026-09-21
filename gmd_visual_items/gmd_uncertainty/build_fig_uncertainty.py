"""Rebuild GMD Fig. `fig:uncertainty` -- the 7-panel Rome uncertainty + global-
sensitivity figure -- from the authoritative NB09 export (N=128 Masselot headline).

    python gmd_visual_items/gmd_uncertainty/build_fig_uncertainty.py --archive /path/to/gmd_nb09_archive.tgz --out-dir /path/to/figures
Panels: (a) annual-mortality ratio to median; (b) daily heat-death profile;
(c) 25-yr net avoided deaths and (d) present-value cost for AC/Trees/EWS
(median + IQR); (e-g) largest observed PAWN screening scores for annual
mortality, EWS net avoided deaths, and the 2050 SVI P90-P10 gap.
"""
import argparse
import tarfile
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from archive_io import load_samples, read_csv, verify_checksum

RUN = "n128_seed42_v3_616fbc2d5131"     # headline Masselot N=128
CITY = "rome"
AC, EWS, TREE = "#d1495b", "#1f77b4", "#2a9d3a"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", required=True, type=Path, help="Verified GMD four-pilot .tgz")
parser.add_argument("--out-dir", required=True, type=Path, help="Directory for fig_uncertainty.png")
args = parser.parse_args()
verify_checksum(args.archive)

with tarfile.open(args.archive, "r:gz") as tar:
    prefix, samples = load_samples(tar, CITY, RUN, 128)

    def rd(name):
        return read_csv(tar, f"{prefix}/{name}_{CITY}_improved_fast.csv")

    fc = rd("unc_freq_curve")
    cba_ac, cba_ews, cba_tr = rd("unc_cba_ac"), rd("unc_cba_ews"), rd("unc_cba_trees")
    pawn_tables = {name: rd(name) for name in ("sens_aai_agg", "sens_cba_ews", "sens_vulnerability")}

def miq(a):
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    if len(a) == 0:
        return np.nan, np.nan, np.nan
    return np.median(a), np.percentile(a, 25), np.percentile(a, 75)

def pawn_top(df, col, n=10):
    df = df[df["si"] == "median"].dropna(subset=[col]).sort_values(col, ascending=False).head(n)
    return df["param"].tolist()[::-1], df[col].to_numpy(float)[::-1]

fig, axes = plt.subplots(4, 2, figsize=(10, 13))
(a, b), (c, d), (e, f), (g, h) = axes

# (a) annual mortality ratio to median
am = samples["annual_deaths"].to_numpy(float); am = am[np.isfinite(am)]
r = am / np.median(am)
p5, p95 = np.percentile(r, 5), np.percentile(r, 95)
a.hist(r, bins=25, color="#8a8a8a", alpha=.85)
a.axvline(1.0, color="k", lw=1)
for x in (p5, p95):
    a.axvline(x, color="k", ls="--", lw=1)
a.set_title("(a) Annual heat mortality (ratio to median)", fontsize=10)
a.set_xlabel("ratio to median"); a.set_ylabel("draws")
a.annotate(f"P5 ×{p5:.2f}", (p5, a.get_ylim()[1]*.9), fontsize=8, ha="right")
a.annotate(f"P95 ×{p95:.2f}", (p95, a.get_ylim()[1]*.9), fontsize=8)

# (b) daily heat-death profile relative to the 95th-percentile day
xs = [50, 80, 90, 95]
mat = fc[["daily_p50", "daily_p80", "daily_p90", "daily_p95"]].to_numpy(float)
norm = mat / mat[:, [3]]
lo, md, hi = (np.percentile(norm, q, axis=0) for q in (5, 50, 95))
b.fill_between(xs, lo, hi, color="#c9c9c9", alpha=.6)
b.plot(xs, md, "-o", color="#404040")
b.set_title("(b) Daily heat-death profile (rel. 95th-pct day)", fontsize=10)
b.set_xlabel("within-year day percentile"); b.set_ylabel("deaths / 95th-pct day")

labels, cols = ["AC", "Trees", "EWS"], [AC, TREE, EWS]
# (c) 25-yr net avoided deaths
navd = [miq(cba_ac["ac_net_avoided_deaths_25y_cum"]),
        miq(cba_tr["tree_avoided_deaths_25y_cum"]),
        miq(cba_ews["ews_net_avoided_deaths_25y_cum"])]
for i, (m, q1, q3) in enumerate(navd):
    c.errorbar(i, m, yerr=[[m - q1], [q3 - m]], fmt="o", color=cols[i], capsize=4, ms=9)
c.set_xticks(range(3)); c.set_xticklabels(labels); c.set_xlim(-.5, 2.5)
c.set_title("(c) 25-yr net avoided deaths", fontsize=10); c.set_ylabel("deaths (median, IQR)")

# (d) 25-yr present-value cost (log)
pvc = [miq(cba_ac["ac_pv_cost_25y"]), miq(cba_tr["tree_pv_cost_25y"]), miq(cba_ews["ews_pv_cost_25y"])]
for i, (m, q1, q3) in enumerate(pvc):
    d.errorbar(i, m, yerr=[[max(m - q1, m*1e-6)], [q3 - m]], fmt="o", color=cols[i], capsize=4, ms=9)
d.set_yscale("log"); d.set_xticks(range(3)); d.set_xticklabels(labels); d.set_xlim(-.5, 2.5)
d.set_title("(d) 25-yr present-value cost (log)", fontsize=10); d.set_ylabel("EUR (median, IQR)")

# (e-g) Ten largest observed screening scores; no outcome-input filtering.
for ax, (name, col, title, color) in zip([e, f, g], [
        ("sens_aai_agg", "aai_agg", "(e) Largest observed PAWN scores:\nannual mortality", "#666666"),
        ("sens_cba_ews", "ews_net_avoided_deaths_25y_cum", "(f) Largest observed PAWN scores:\nEWS net avoided deaths", EWS),
        ("sens_vulnerability", "vuln_2050_svi_p90_p10_gap", "(g) Largest observed PAWN scores:\n2050 SVI P90-P10 gap", "#7a4fa3")]):
    params, vals = pawn_top(pawn_tables[name], col)
    ax.barh(range(len(params)), vals, color=color, alpha=.85)
    ax.set_yticks(range(len(params))); ax.set_yticklabels(params, fontsize=7)
    ax.set_title(title, fontsize=10); ax.set_xlabel("median PAWN KS statistic")

h.axis("off")
h.legend(handles=[Patch(color=AC, label="AC"), Patch(color=TREE, label="Trees"),
                  Patch(color=EWS, label="EWS")], loc="center", frameon=False, fontsize=11,
         title="Adaptation pathway (c, d)")

fig.suptitle(f"Uncertainty and global sensitivity — {CITY.title()} (N=128 Masselot headline)",
             fontsize=12, y=0.995)
fig.tight_layout(rect=[0, 0, 1, 0.99])
args.out_dir.mkdir(parents=True, exist_ok=True)
out = args.out_dir / "fig_uncertainty.png"
fig.savefig(out, dpi=200, bbox_inches="tight")
plt.close(fig)
print("saved:", out)
print(f"(a) {CITY.title()} P5/P95 ratios: ×{p5:.2f} / ×{p95:.2f}")
