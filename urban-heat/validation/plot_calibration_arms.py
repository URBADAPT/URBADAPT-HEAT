"""Modelled vs Masselot-estimated heat deaths, one facet per calibration arm.

Keeps the two corrections attributable before either is merged into the default spec:

    none      uncorrected (the published spec)
    variance  variance-corrected impact functions only
    era5      ERA5-Land reference bias correction only
    both      both corrections

Form: a predicted-vs-estimated scatter against a 1:1 line is the right shape for "how
close is the model to the reference", and small multiples beat four colours on one panel
because the city clouds overlap almost completely - position carries the arm, so there is
no categorical palette here to validate.

Two rows because the two measures answer different questions and must not share an axis:

  total deaths   comparable only where the modelled domain and Masselot's URAU cover a
                 similar population
  deaths/100k    robust to a difference in extent, as long as the rate changes little
                 between the core and the edge; the denominator is the population of the
                 modelled area (modelled_population.py), which the deaths are summed over

Only arms whose output tree exists are drawn, so this is runnable before all four exist.

Usage:
    python validation/plot_calibration_arms.py
    python validation/plot_calibration_arms.py --out-dir validation/outputs
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

# Where each arm's per-city tables live. Override any of these with
# CALIB_ARM_<ARM>=<path>; arms whose path is absent are skipped with a note.
ARM_PATHS = {
    "none": r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results",
    "variance": r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\arm_variance",
    "era5": r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\arm_era5",
    "both": r"C:\Users\giaco\OneDrive - IIASA\Documents\juno\corrected40",
}
ARM_LABEL = {
    "none": "Not calibrated",
    "variance": "Variance correction only",
    "era5": "ERA5-Land correction only",
    "both": "Both corrections",
}
# Cities worth naming: the ones the conclusions turn on. Deliberately short - in the
# uncorrected panels the cloud is dense enough that five labels collide, and a label
# nobody needs is worse than no label.
#   dublin  the extreme cool failure (0.03x uncorrected)
#   athens  the one outlier neither correction explains
#   paris   the worst geography mismatch, so its per-capita point is not comparable
ANNOTATE = {"athens", "dublin", "paris"}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from modelled_population import modelled_population  # noqa: E402
INK, INK2, MUT = "#1a1a18", "#55554f", "#6b6b66"
REF = "#b9b8b2"
# The paper's climate-class colours and point rim (visual_items/multi_city/R/_helpers.R).
CLASS_COLORS = {"Hot": "#9E1B32", "Temperate": "#D1A834", "Cool": "#A7BACB"}
OUTLINE = "#404040"
CITY_META = os.path.join(ROOT, "..", "visual_items", "multi_city", "compare", "v3c", "tables",
                         "city_metadata.csv")
# Font sizes for a figure drawn about 11.7 in wide and printed at text width (59%):
# ticks and notes print at about 6 pt, axis titles at about 7 pt, column titles at 8 pt.
FS_TICK, FS_AXIS, FS_TITLE, FS_NOTE = 10.5, 12, 13.5, 10.5


def arm_frame(base: str, geo: pd.DataFrame, mas: pd.DataFrame) -> pd.DataFrame:
    """Per-city modelled 2020 deaths + population for one arm, joined to Masselot."""
    rows = []
    for _, g in geo.iterrows():
        s = g.slug
        f = os.path.join(base, s, "tables", f"annual_heat_deaths_baseline_current_ac_{s}.csv")
        # The population of the modelled area, which the deaths are summed over, is
        # correction-independent, so take it from whichever tree has the EWS tables.
        pop = np.nan
        for tree in (base, *ARM_PATHS.values()):
            pop = modelled_population(os.path.join(tree, s, "tables"), s)
            if np.isfinite(pop):
                break
        if not (os.path.exists(f) and np.isfinite(pop)):
            continue
        d = pd.read_csv(f)
        v = d.loc[d.year == 2020, "deaths_overall"]
        if v.empty:
            continue
        rows.append({"slug": s, "urau_code": g.urau_code,
                     "deaths": float(v.iloc[0]),
                     "pop": pop})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t = t.merge(mas, on="urau_code", how="left")
    t["rate"] = t.deaths / t["pop"] * 1e5
    return t


def panel(ax, x, y, labels, classes, names, xlabel, ylabel, title, note):
    import matplotlib.pyplot as plt  # noqa: F401
    from matplotlib import patheffects

    good = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
    x, y = x[good], y[good]
    labels = [l for l, k in zip(labels, good) if k]
    classes = [c for c, k in zip(classes, good) if k]
    names = [n for n, k in zip(names, good) if k]
    lo = min(x.min(), y.min()) * 0.55
    hi = max(x.max(), y.max()) * 1.8
    ax.plot([lo, hi], [lo, hi], color=REF, lw=2, zorder=1, solid_capstyle="round")
    for f, ls in ((2.0, (0, (4, 3))), (0.5, (0, (4, 3)))):
        ax.plot([lo, hi], [lo * f, hi * f], color=REF, lw=1, ls=ls, zorder=1)
    # Points in the climate-class colours with a dark rim, as in the paper's other figures.
    ax.scatter(x, y, s=46, facecolor=[CLASS_COLORS.get(c, "#999999") for c in classes],
               edgecolor=OUTLINE, linewidth=0.5, alpha=0.9, zorder=3)
    for xi, yi, li, ni in zip(x, y, labels, names):
        if li in ANNOTATE:
            ax.annotate(ni, (xi, yi), textcoords="offset points", xytext=(7, 3),
                        fontsize=FS_NOTE, color=INK2, zorder=4,
                        path_effects=[patheffects.withStroke(linewidth=2.5, foreground="white")])
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title, fontsize=FS_TITLE, color=INK, pad=8, loc="left", fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=FS_AXIS, color=INK2)
    ax.set_ylabel(ylabel, fontsize=FS_AXIS, color=INK2)
    ax.tick_params(labelsize=FS_TICK, colors=INK2, length=3)
    ax.grid(True, which="major", color=REF, lw=0.6, alpha=0.5)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(REF)
    ax.annotate(note, xy=(0.035, 0.965), xycoords="axes fraction", va="top",
                fontsize=FS_NOTE, color=INK2)


def stats(x, y):
    good = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
    r = y[good] / x[good]
    return (f"n = {good.sum()}, median ratio {np.median(r):.2f}\n"
            f"within a factor of 2: {int(((r > .5) & (r < 2)).sum())}/{good.sum()}\n"
            f"spread {r.max()/r.min():.0f}\u00d7")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=os.path.join(HERE, "outputs"))
    ap.add_argument("--restrict", choices=("all", "common"), default="all",
                    help="'common' keeps only cities present in EVERY available arm, so "
                         "the arms are compared on the same sample; 'all' lets each arm "
                         "use everything it has")
    ap.add_argument("--arms", nargs="*", default=None,
                    help="limit to these arms (default: every arm whose tree exists)")
    ap.add_argument("--suffix", default="", help="appended to the output filenames")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.lines import Line2D
    if any(f.name == "Arial" for f in font_manager.fontManager.ttflist):
        matplotlib.rcParams["font.family"] = "Arial"   # the R figures' sans on Windows
    meta = pd.read_csv(CITY_META)[["city", "city_label", "climate_cluster"]]
    cls = dict(zip(meta.city, meta.climate_cluster))
    nice = dict(zip(meta.city, meta.city_label))

    geo = pd.read_csv(os.path.join(HERE, "reference_geographies.csv"), dtype=str)
    mas = pd.read_csv(os.path.join(ROOT, "10288665", "results", "city.csv"))[
        ["URAU_CODE", "excess_heat_est", "rawrate_heat_est"]
    ].rename(columns={"URAU_CODE": "urau_code",
                      "excess_heat_est": "mas_deaths",
                      "rawrate_heat_est": "mas_rate"})

    want = args.arms or list(ARM_PATHS)
    arms, frames = [], {}
    for arm in want:
        if arm not in ARM_PATHS:
            print(f"[skip] unknown arm '{arm}'")
            continue
        base = os.environ.get(f"CALIB_ARM_{arm.upper()}", ARM_PATHS[arm])
        if not os.path.isdir(base):
            print(f"[skip] arm '{arm}': no tree at {base}")
            continue
        t = arm_frame(base, geo, mas)
        if t.empty:
            print(f"[skip] arm '{arm}': tree present but no city tables")
            continue
        frames[arm] = t
        arms.append(arm)
        print(f"[ok]   arm '{arm}': {len(t)} cities")
    if not arms:
        print("[error] no arms available")
        return 2

    # An arm pulled only part-way would otherwise be compared on a different sample from
    # the others, and a subset that happens to be cool (or hot) shifts its median for
    # that reason alone rather than because of the calibration.
    sizes = {a: len(frames[a]) for a in arms}
    if len(set(sizes.values())) > 1:
        print(f"[warn] arms cover different city counts: {sizes}")
        if args.restrict == "all":
            print("       comparing each on its own sample - pass --restrict common "
                  "to force a like-for-like set")
    if args.restrict == "common":
        common = set.intersection(*(set(frames[a].slug) for a in arms))
        for a in arms:
            frames[a] = frames[a][frames[a].slug.isin(common)].reset_index(drop=True)
        print(f"[restrict] common to all arms: {len(common)} cities")

    n = len(arms)
    fig, axes = plt.subplots(2, n, figsize=(3.7 * n + 0.6, 7.8), squeeze=False)
    fig.patch.set_facecolor("white")
    for j, arm in enumerate(arms):
        t = frames[arm]
        slugs = list(t.slug)
        classes = [cls.get(c) for c in slugs]
        names = [nice.get(c, c) for c in slugs]
        panel(axes[0][j], t.mas_deaths.to_numpy(float), t.deaths.to_numpy(float),
              slugs, classes, names, "Masselot estimate (deaths per year)", "Modelled (deaths per year)",
              f"{ARM_LABEL[arm]}", stats(t.mas_deaths.to_numpy(float), t.deaths.to_numpy(float)))
        panel(axes[1][j], t.mas_rate.to_numpy(float), t.rate.to_numpy(float),
              slugs, classes, names, "Masselot estimate (per 100,000)", "Modelled (per 100,000)",
              "", stats(t.mas_rate.to_numpy(float), t.rate.to_numpy(float)))
    axes[0][0].set_ylabel("Total deaths\n\nModelled (deaths per year)", fontsize=FS_AXIS, color=INK)
    axes[1][0].set_ylabel("Deaths per 100,000\n\nModelled (per 100,000)", fontsize=FS_AXIS, color=INK)
    ncity = len(frames[arms[0]])
    scope = (f"common sample of {ncity} cities" if args.restrict == "common"
             else f"each arm on its own sample ({', '.join(f'{a} n={len(frames[a])}' for a in arms)})")
    # No in-figure title: the caption carries the description, the line key and the
    # sample. One climate-class legend at the foot, as in the paper's other figures.
    print(f"[scope] {scope}")
    handles = [Line2D([], [], marker="o", linestyle="", markersize=8, markerfacecolor=CLASS_COLORS[k],
                      markeredgecolor=OUTLINE, markeredgewidth=0.5, label=k) for k in CLASS_COLORS]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=FS_AXIS,
               title="Climate class", title_fontsize=FS_AXIS, bbox_to_anchor=(0.5, 0.0))
    fig.tight_layout(rect=(0, 0.08, 1, 1))

    os.makedirs(args.out_dir, exist_ok=True)
    for ext in ("png", "pdf"):
        p = os.path.join(args.out_dir, f"calibration_arms{args.suffix}.{ext}")
        fig.savefig(p, dpi=300 if ext == "png" else None, facecolor="white")
        print(f"[write] {p}")

    rows = []
    for arm in arms:
        t = frames[arm]
        for measure, xc, yc in (("total", "mas_deaths", "deaths"),
                                ("per_capita", "mas_rate", "rate")):
            x, y = t[xc].to_numpy(float), t[yc].to_numpy(float)
            g = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
            r = y[g] / x[g]
            rows.append({"arm": arm, "measure": measure, "n": int(g.sum()),
                         "median_ratio": np.median(r), "spread": r.max() / r.min(),
                         "within_2x": int(((r > .5) & (r < 2)).sum()),
                         "mean_abs_log": np.abs(np.log(r)).mean()})
    s = pd.DataFrame(rows)
    print()
    print(s.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    s.to_csv(os.path.join(args.out_dir, f"calibration_arms_summary{args.suffix}.csv"),
             index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
