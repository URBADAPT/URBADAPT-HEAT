"""Rebuild the pilot-city uncertainty figure (`fig:uncertainty`) -- the 8-panel Rome
uncertainty + global-sensitivity figure -- from the NB09 campaigns (N=128 Masselot
headline, plus the two N=128 Burke IF-conditional ensembles).

    python visual_items/pilot_cities/uncertainty/build_fig_uncertainty.py --tree <pilot output tree> --out-dir <dir>
    python visual_items/pilot_cities/uncertainty/build_fig_uncertainty.py --archive /path/to/nb09_archive.tgz --out-dir <dir>
Panels: (a) annual-mortality ratio to median; (b) daily heat-death profile;
(c) 25-yr net avoided deaths (Masselot and both Burke families) and (d)
present-value cost for AC/Trees/EWS (median + IQR); (e-g) largest observed PAWN
screening scores for annual mortality, EWS net avoided deaths, and the 2050 SVI
P90-P10 gap; (h) annual mortality by impact-function family. The Burke ensembles
reuse the headline draws with only IF_FAMILY_IDX changed, so costs are identical
across families and (d) shows Masselot only.
"""
import argparse
import tarfile
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

from archive_io import DirArchive, campaigns_in_tree, load_samples, read_csv, resolve_campaign, verify_checksum

# The packaged archive (--archive): headline run, and the pattern of the run that
# holds both Burke ensembles; --tree takes every family from one campaign instead.
RUN = "n128_seed42_v3_616fbc2d5131"     # headline Masselot N=128
BURKE_PATTERN = "*_n128_burke_seed42_v3_616fbc2d5131"
# (key, label, campaign, conditional branch, marker, filled)
FAMILIES = [("masselot", "Masselot\n(headline)", RUN, None, "o", True),
            ("burke_polynomial", "Burke\npolynomial", BURKE_PATTERN, "burke_polynomial", "s", False),
            ("burke_powerlaw", "Burke\npower-law", BURKE_PATTERN, "burke_powerlaw", "^", False)]
CITY = "rome"
AC, EWS, TREE = "#d1495b", "#1f77b4", "#2a9d3a"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, help="Verified four-pilot NB09 .tgz")
# The archive is a packaged, checksummed campaign. --tree reads the same layout
# straight out of an output tree, so a figure can be rebuilt from a run before it
# has been packaged - which is otherwise the long pole after a recalibration.
parser.add_argument("--tree", type=Path,
                    help="Pilot output tree root (<variant dir>), "
                         "read in place of a packaged archive")
parser.add_argument("--campaign", help="Campaign id within --tree; the newest is used "
                                       "when omitted")
parser.add_argument("--out-dir", required=True, type=Path, help="Directory for fig_uncertainty.png")
args = parser.parse_args()
if (args.archive is None) == (args.tree is None):
    parser.error("give exactly one of --archive or --tree")

if args.tree is not None:
    found = campaigns_in_tree(args.tree, CITY)
    camp = args.campaign or (found[0] if found else None)
    if camp is None:
        parser.error(f"no campaign under {args.tree}/{CITY}/tables/uncertainty_runs")
    print(f"[tree] {args.tree}  city {CITY}  campaign {camp}"
          + (f"   (of {len(found)}: {', '.join(found)})" if len(found) > 1 else ""))
    # Every family comes from the one campaign this run produced, rather than the
    # two separate campaign ids the packaged archive carries.
    FAMILIES = [(k, lab, camp, br, m, f) for k, lab, _, br, m, f in FAMILIES]
else:
    verify_checksum(args.archive)

with (DirArchive(args.tree) if args.tree is not None
      else tarfile.open(args.archive, "r:gz")) as tar:
    if args.tree is None:
        burke_run = resolve_campaign(tar, CITY, BURKE_PATTERN)
        FAMILIES = [(k, lab, burke_run if br else camp, br, m, f) for k, lab, camp, br, m, f in FAMILIES]
    fam = {}
    for key, _, campaign, branch, _, _ in FAMILIES:
        fprefix, fsamples = load_samples(tar, CITY, campaign, 128, branch=branch)
        fam[key] = {"prefix": fprefix, "samples": fsamples,
                    **{n: read_csv(tar, f"{fprefix}/{n}_{CITY}_improved_fast.csv")
                       for n in ("unc_cba_ac", "unc_cba_ews", "unc_cba_trees")}}
    prefix, samples = fam["masselot"]["prefix"], fam["masselot"]["samples"]

    def rd(name):
        return read_csv(tar, f"{prefix}/{name}_{CITY}_improved_fast.csv")

    fc = rd("unc_freq_curve")
    cba_ac, cba_ews, cba_tr = (fam["masselot"][n] for n in ("unc_cba_ac", "unc_cba_ews", "unc_cba_trees"))
    pawn_tables = {name: rd(name) for name in ("sens_aai_agg", "sens_cba_ews", "sens_vulnerability")}

# The Burke runs must be the headline draws with only the IF family swapped.
coords = [c for c in samples.columns if c.isupper() and c != "IF_FAMILY_IDX"]
for key in ("burke_polynomial", "burke_powerlaw"):
    other = fam[key]["samples"].set_index("sample_idx").loc[samples["sample_idx"], coords]
    if not np.array_equal(other.to_numpy(), samples[coords].to_numpy()):
        raise ValueError(f"{key}: LHS draws differ from the headline run beyond IF_FAMILY_IDX")

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
a.yaxis.set_major_locator(MaxNLocator(integer=True))
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
# (c) 25-yr net avoided deaths; Burke families offset to the right of Masselot
outcomes = [("unc_cba_ac", "ac_net_avoided_deaths_25y_cum"),
            ("unc_cba_trees", "tree_avoided_deaths_25y_cum"),
            ("unc_cba_ews", "ews_net_avoided_deaths_25y_cum")]
for j, (key, _, _, _, marker, filled) in enumerate(FAMILIES):
    for i, (table, col) in enumerate(outcomes):
        m, q1, q3 = miq(fam[key][table][col])
        c.errorbar(i + (j - 1) * .24, m, yerr=[[m - q1], [q3 - m]], fmt=marker, color=cols[i],
                   mfc=cols[i] if filled else "white", mew=1.5, capsize=3,
                   ms=9 if filled else 7)
c.set_xticks(range(3)); c.set_xticklabels(labels); c.set_xlim(-.5, 2.5)
c.set_title("(c) 25-yr net avoided deaths", fontsize=10); c.set_ylabel("deaths (median, IQR)")
c.legend(handles=[Line2D([], [], ls="", marker=mk, color="#404040", mfc="#404040" if fl else "white",
                         mew=1.5, ms=7, label=lb.replace("\n", " "))
                  for _, lb, _, _, mk, fl in FAMILIES],
         loc="upper left", fontsize=7, frameon=False, handletextpad=.3)

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

# (h) annual mortality by impact-function family: draws, IQR, P5-P95, median
def deaths(v: float) -> str:
    """Whole deaths, but keep sub-one Burke tails readable instead of printing 0."""
    return f"{v:,.0f}" if v >= 1 else f"{v:.2g}"


rng = np.random.default_rng(0)
fam_stats = {}
for j, (key, label, _, _, marker, filled) in enumerate(FAMILIES):
    y = fam[key]["samples"]["annual_deaths"].to_numpy(float)
    p5_, q1, med, q3, p95_ = np.percentile(y, [5, 25, 50, 75, 95])
    fam_stats[key] = (med, p5_, p95_)
    tone = "#404040" if filled else "#8a8a8a"
    h.scatter(j + rng.uniform(-.13, .13, len(y)), y, s=7, color=tone, alpha=.35, lw=0)
    h.vlines(j + .24, p5_, p95_, color=tone, lw=1)
    h.vlines(j + .24, q1, q3, color=tone, lw=4)
    h.plot(j + .24, med, marker, color=tone, mfc=tone if filled else "white", mew=1.5, ms=7)
h.set_yscale("log")
h.set_xticks(np.arange(3) + .1)
h.set_xticklabels([f"{f[1]}\n{deaths(fam_stats[f[0]][0])} [{deaths(fam_stats[f[0]][1])}–{deaths(fam_stats[f[0]][2])}]"
                   for f in FAMILIES], fontsize=8)
h.set_xlim(-.4, 2.6)
h.set_title("(h) Annual heat mortality by IF family", fontsize=10)
h.set_xlabel("median [P5–P95]", fontsize=8)
h.set_ylabel("deaths per year (log)")

fig.suptitle(f"Uncertainty and global sensitivity — {CITY.title()} "
             "(N=128 Masselot headline; Burke IF-conditional ensembles in c, h)",
             fontsize=12, y=0.995)
fig.tight_layout(rect=[0, 0, 1, 0.99])
args.out_dir.mkdir(parents=True, exist_ok=True)
out = args.out_dir / "fig_uncertainty.png"
fig.savefig(out, dpi=200, bbox_inches="tight")
plt.close(fig)
print("saved:", out)
print(f"(a) {CITY.title()} P5/P95 ratios: x{p5:.2f} / x{p95:.2f}")
for key, (med, lo_, hi_) in fam_stats.items():
    print(f"(h) {key}: median {deaths(med)}, P5-P95 {deaths(lo_)}-{deaths(hi_)}")
