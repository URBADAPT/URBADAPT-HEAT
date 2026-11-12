"""CLI: emit three pilot-city tables of the model description as LaTeX fragments.

    calibration_arms.tex           modelled / published 2020 baseline heat mortality per
                                   100 000 under the four hazard-curve correction arms
                                   (Table tab:calibration_arms, 40 cities)
    city_results.tex               illustrative pilot-city outputs: applied warming, heat
                                   deaths, avoided deaths, cost per avoided death
                                   (Table tab:city_results)
    vulnerability_params_city.tex  dynamic-vulnerability parameters of the pilot configs
                                   (appendix Table tab:vulnerability_params_city)

Each fragment holds the ``tabular`` environment only. The caption and label stay in the
manuscript's table float, which includes the fragment with ``\\input`` in place of its
inline tabular. These three tables used to be typed by hand from script output.

Sources
-------
calibration_arms
    The ``per_capita`` rows of ``calibration_arms_summary.csv``, written by
    ``urban-heat/validation/plot_calibration_arms.py``. That script needs the four 40-city
    arm trees (no correction, variance only, ERA5-Land reference only, both; paths in its
    ARM_PATHS, overridable with CALIB_ARM_NONE / _VARIANCE / _ERA5 / _BOTH), the Masselot
    city estimates ``urban-heat/10288665/results/city.csv`` and
    ``urban-heat/validation/reference_geographies.csv``. Its per-capita denominator is
    the population of the modelled area (``validation/modelled_population.py``).
    Median ratio and spread are taken over modelled/published per-100k ratios; spread is
    the largest over the smallest city ratio; "within a factor of two" counts 0.5 < r < 2.

city_results
    A pilot output tree (``--tree`` or env PILOT_TREE; one folder per city), per city:

    * ``interim/annual_heat_deaths_generic_<city>_<family>.csv``: climate-only branch
      before current-AC attenuation, summed over age groups (family = ``if_main_family``
      of ``interim/if_family_manifest_<city>.json``).
    * ``tables/<city>_cea_summary.csv``: 25-year undiscounted avoided deaths and
      present-value cost of AC (GROSS, NET), EWS (central) and trees (base O&M). The
      aggregate standalone ratio is the summed PV cost of AC (NET), EWS (central) and
      trees (base O&M) over their summed avoided deaths.
    * ``tables/<city>_ews_parameters.json``: EWS interpretation, marginal (m) or
      counterfactual (c).
    * Applied annual-mean dT2M, 2050-2020: the day-weighted (2050 calendar) mean of the
      twelve monthly increments Notebook 02 adds to the baseline calendar for 2050
      (``read_monthly_delta_C``; scenario, band and variable from the city config:
      CurPol, central, tas). The 2020 hazard carries no increment. The increments are
      read from ``<base_dir>/T2MmeanDeltas/climate_change_provide_markups_bands.csv``
      when that NB02 input is present; otherwise they are recovered from the hazard the
      run actually applied, ``interim/hazard_events_T2M_daily_<city>.csv``: each 2050 day
      minus the same calendar day of 2020 in ``mean_intensity_citymask_degC``. The ERA5
      reference shift cancels in that difference and the increment is uniform in space,
      so the difference is the monthly increment (checked constant within each month).

vulnerability_params_city
    ``urban-heat/configs/<city>.yml``, block ``vulnerability.dynamic``: ``drmkc.region_id``;
    ``k.foreign_born`` / ``k.unemployment``; ``phi.<component>_2030`` / ``_2050``;
    ``thermal_projection.retrofit_rate_per_year`` (r_c), ``new_build_vulnerability``,
    ``growth_sensitivity`` and ``growth_cap`` (maximum new-build share).

Number formats follow the manuscript row by row, including where rows differ (EWS cost
to three decimals, tree cost to two decimals below EUR 100 million).

Usage (from the ``visual_items/pilot_cities/`` directory)::

    python -m workflow_tables.make_pilot_tables --tree <pilot output tree>
    python -m workflow_tables.make_pilot_tables --tree <tree> --out /path/to/overleaf/tables
    python -m workflow_tables.make_pilot_tables --tables vulnerability_params_city
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

try:
    from .make_uncertainty_matrix import CITY_LABEL, PILOTS, find_repo_root
except ImportError:  # run as a plain script rather than with -m
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from make_uncertainty_matrix import CITY_LABEL, PILOTS, find_repo_root

TABLES = ("calibration_arms", "city_results", "vulnerability_params_city")
BASE_YEAR, END_YEAR = 2020, 2050


def load_configs(root: Path) -> dict:
    out = {}
    for slug in PILOTS:
        path = root / "configs" / f"{slug}.yml"
        if not path.exists():
            raise SystemExit(f"missing config: {path}")
        out[slug] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return out


def header(label: str, sources: list[str]) -> list[str]:
    return [
        "%% " + "=" * 74,
        "%% GENERATED FILE -- do not edit by hand.",
        "%% Produced by visual_items/pilot_cities/workflow_tables/make_pilot_tables.py",
        f"%% Table: {label} (tabular only; caption and label stay in the host float)",
        *(f"%% Source: {s}" for s in sources),
        "%% Regenerate with (from visual_items/pilot_cities/):",
        "%%     python -m workflow_tables.make_pilot_tables --tree <pilot tree> --out <tables dir>",
        "%% " + "=" * 74,
    ]


# --------------------------------------------------------------------------- #
# calibration arms
# --------------------------------------------------------------------------- #

ARM_ROWS = (("none", "No correction"), ("variance", "Variance only"),
            ("era5", "Reference only"), ("both", "Both corrections"))


def calibration_arms(csv: Path) -> list[str]:
    if not csv.exists():
        raise SystemExit(
            f"missing {csv}\n  produce it with: python urban-heat/validation/"
            "plot_calibration_arms.py   (or pass --calibration-csv)")
    s = pd.read_csv(csv)
    s = s[s.measure == "per_capita"].set_index("arm")
    missing = [a for a, _ in ARM_ROWS if a not in s.index]
    if missing:
        raise SystemExit(f"{csv}: no per_capita row for arm(s) {missing}")
    lines = [r"\begin{tabular}{lccc}", r"\toprule",
             r"Configuration & Median ratio & Spread & Within a factor of two \\",
             r"\midrule"]
    for arm, label in ARM_ROWS:
        r = s.loc[arm]
        lines.append(f"{label:<25}& {r.median_ratio:.2f} & {r.spread:3.0f} & "
                     f"{int(r.within_2x)}/{int(r.n)} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return lines


# --------------------------------------------------------------------------- #
# pilot-city results
# --------------------------------------------------------------------------- #

def _need(path: Path) -> Path:
    if not path.exists():
        raise SystemExit(f"missing pilot output: {path}")
    return path


def monthly_increments_from_hazard(tree: Path, slug: str) -> np.ndarray:
    """Monthly increments NB02 applied for END_YEAR, recovered from the run's hazard."""
    h = pd.read_csv(_need(tree / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv"),
                    parse_dates=["date"])
    h["md"] = list(zip(h.date.dt.month, h.date.dt.day))
    col = "mean_intensity_citymask_degC"
    end = h[h.date.dt.year == END_YEAR].set_index("md")[col]
    base = h[h.date.dt.year == BASE_YEAR].set_index("md")[col]
    diff = end - base.reindex(end.index)
    if diff.isna().any() or len(diff) != 365:
        raise SystemExit(f"{slug}: hazard series lacks a full {BASE_YEAR}/{END_YEAR} calendar")
    month = np.array([m for m, _ in diff.index])
    monthly = np.array([diff[month == m].mean() for m in range(1, 13)])
    dev = max(np.abs(diff[month == m] - monthly[m - 1]).max() for m in range(1, 13))
    if dev > 1e-4:
        raise SystemExit(f"{slug}: {END_YEAR}-{BASE_YEAR} hazard difference varies within "
                         f"a month by {dev:.2e} degC, so it is not a monthly increment")
    return monthly


def monthly_increments_from_table(path: Path, cfg: dict, slug: str) -> np.ndarray:
    """NB02 read_monthly_delta_C for END_YEAR, on the band-aware delta table."""
    clim = cfg.get("climate", {})
    d = pd.read_csv(path)
    aliases = [str(a).lower() for a in (cfg.get("cooling_city_aliases") or [slug])]
    sub = d[(d.pct_band == clim.get("t2m_delta_pct_band_default", "central"))
            & d.city.str.lower().isin(aliases) & (d.year == END_YEAR)
            & (d.clim_scen == clim.get("t2m_clim_scen_default", "CurPol"))
            & (d["var"] == clim.get("t2m_var", "tas"))]
    if sub.month.nunique() != 12:
        raise SystemExit(f"{path}: expected 12 monthly deltas for {slug} {END_YEAR}")
    return sub.set_index("month").loc[range(1, 13), "delta"].to_numpy(float)


def applied_warming(tree: Path, slug: str, cfg: dict, root: Path) -> tuple[float, np.ndarray, str]:
    ndays = np.array([calendar.monthrange(END_YEAR, m)[1] for m in range(1, 13)])
    table = root / cfg.get("base_dir", "") / "T2MmeanDeltas" / \
        "climate_change_provide_markups_bands.csv"
    hazard = tree / slug / "interim" / f"hazard_events_T2M_daily_{slug}.csv"
    if table.exists():
        monthly, src = monthly_increments_from_table(table, cfg, slug), str(table)
        if hazard.exists():
            check = monthly_increments_from_hazard(tree, slug)
            if np.abs(check - monthly).max() > 5e-3:
                print(f"[warn] {slug}: delta table and applied hazard differ by up to "
                      f"{np.abs(check - monthly).max():.3f} degC; the tree was not run "
                      "on this table")
    else:
        monthly, src = monthly_increments_from_hazard(tree, slug), str(hazard)
    return float((monthly * ndays).sum() / ndays.sum()), monthly, src


def city_numbers(tree: Path, slug: str, cfg: dict, root: Path) -> dict:
    r: dict = {}
    interim, tables = tree / slug / "interim", tree / slug / "tables"

    fam = "masselot_tail_varcorr"
    man = interim / f"if_family_manifest_{slug}.json"
    if man.exists():
        fam = json.loads(man.read_text(encoding="utf-8")).get("if_main_family", fam)
    g = pd.read_csv(_need(interim / f"annual_heat_deaths_generic_{slug}_{fam}.csv"))
    tot = g.set_index("year")[[c for c in g.columns if c != "year"]].sum(axis=1)
    r["clim_2020"], r["clim_2050"] = float(tot[BASE_YEAR]), float(tot[END_YEAR])

    c = pd.read_csv(_need(tables / f"{slug}_cea_summary.csv")).set_index("Policy")
    labels = {"ac_gross": "AC (GROSS)", "ac_net": "AC (NET)",
              "ews": "EWS (central)", "trees": "Trees (base O&M)"}
    if not set(labels.values()) <= set(c.index):
        raise SystemExit(f"{slug}: cea_summary lacks {set(labels.values()) - set(c.index)}")
    for k, lab in labels.items():
        r[f"{k}_deaths"] = float(c.loc[lab, "Avoided_Deaths_25y"])
        r[f"{k}_cpd"] = float(c.loc[lab, "Cost_per_Death_EUR"])
    standalone = [labels["ac_net"], labels["ews"], labels["trees"]]
    r["aggregate_cpd"] = float(c.loc[standalone, "PV_Cost_EUR"].sum()
                               / c.loc[standalone, "Avoided_Deaths_25y"].sum())

    ews_json = tables / f"{slug}_ews_parameters.json"
    interp = (json.loads(ews_json.read_text(encoding="utf-8")).get("interpretation")
              if ews_json.exists() else None) or cfg.get("ews", {}).get("interpretation")
    if interp not in ("marginal", "counterfactual"):
        raise SystemExit(f"{slug}: unknown EWS interpretation {interp!r}")
    r["ews_sup"] = interp[0]

    r["dt"], r["dt_monthly"], r["dt_source"] = applied_warming(tree, slug, cfg, root)
    return r


def deaths(v: float) -> str:
    return f"{v:.1f}" if abs(v) < 10 else f"{v:.0f}"


def meur(v: float) -> str:  # aggregate and AC rows: 2 dp below EUR 10 million, else 1 dp
    v /= 1e6
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


def meur_ews(v: float) -> str:
    return f"{v / 1e6:.3f}"


def meur_trees(v: float) -> str:
    v /= 1e6
    return f"{v:.2f}" if abs(v) < 100 else f"{v:.0f}"


def city_results(nums: dict) -> list[str]:
    ind, cont = "  ", "      "
    lines = [ind + r"\begin{tabular}{l cccc}",
             ind + r"\textbf{Metric} & "
             + " & ".join(rf"\textbf{{{CITY_LABEL[s]}}}" for s in PILOTS) + r" \\ \hline"]

    def row(label, fmt, tail=r" \\"):
        lines.append(ind + label)
        lines.append(cont + "& " + " & ".join(fmt(nums[s]) for s in PILOTS) + tail)

    row(r"Applied annual-mean $\Delta$T2M, 2050$-$2020 ($^\circ$C)",
        lambda r: f"{r['dt']:.2f}")
    row(r"Heat deaths before current-AC attenuation, 2020 (yr$^{-1}$)",
        lambda r: deaths(r["clim_2020"]))
    row(r"Heat deaths before current-AC attenuation, 2050 (yr$^{-1}$)",
        lambda r: deaths(r["clim_2050"]))
    row(r"AC avoided deaths, 25\,y (gross / net)",
        lambda r: f"{deaths(r['ac_gross_deaths'])} / {deaths(r['ac_net_deaths'])}")
    row(r"EWS net avoided deaths, 25\,y",
        lambda r: f"{deaths(r['ews_deaths'])}$^{{{r['ews_sup']}}}$")
    row(r"Trees avoided deaths, 25\,y", lambda r: deaths(r["trees_deaths"]),
        tail=r" \\ \hline")
    lines.append(ind + r"Cost per avoided death (\euro{} million) & & & & \\")
    row(r"\quad Aggregate standalone ratio", lambda r: meur(r["aggregate_cpd"]))
    row(r"\quad Air conditioning (net)", lambda r: meur(r["ac_net_cpd"]))
    row(r"\quad Early warning system", lambda r: meur_ews(r["ews_cpd"]))
    row(r"\quad Urban trees", lambda r: meur_trees(r["trees_cpd"]))
    lines.append(ind + r"\end{tabular}")
    return lines


# --------------------------------------------------------------------------- #
# vulnerability parameters
# --------------------------------------------------------------------------- #

def vulnerability_params(cfgs: dict) -> list[str]:
    dyn = {}
    for s in PILOTS:
        d = (cfgs[s].get("vulnerability") or {}).get("dynamic")
        if not d:
            raise SystemExit(f"{s}: config has no vulnerability.dynamic block")
        dyn[s] = d

    def f2(v):
        return f"{float(v):.2f}"

    def phi(comp):
        return lambda d: f"{f2(d['phi'][comp + '_2030'])}/{f2(d['phi'][comp + '_2050'])}"

    rows = (
        ("DRMKC NUTS3 region", lambda d: str(d["drmkc"]["region_id"])),
        (r"\(k_{\mathrm{oth}}\)", lambda d: f2(d["k"]["foreign_born"])),
        (r"\(k_{\mathrm{ne}}\)", lambda d: f2(d["k"]["unemployment"])),
        (r"\(\phi_{\mathrm{oth},2030}/\phi_{\mathrm{oth},2050}\)", phi("foreign_born")),
        (r"\(\phi_{\mathrm{ne},2030}/\phi_{\mathrm{ne},2050}\)", phi("unemployment")),
        (r"Effective thermal-decay rate \(r_c\) (yr\(^{-1}\))",
         lambda d: f"{float(d['thermal_projection']['retrofit_rate_per_year']):.4f}"),
        (r"New-build vulnerability \(z_{\mathrm{new}}\)",
         lambda d: f2(d["thermal_projection"]["new_build_vulnerability"])),
        ("Growth sensitivity", lambda d: f2(d["thermal_projection"]["growth_sensitivity"])),
        ("Maximum new-build share", lambda d: f2(d["thermal_projection"]["growth_cap"])),
    )
    ind, cell = "    ", "         "
    lines = [ind + r"\begin{tabular}{lcccc}",
             cell + r"\textbf{Parameter} & "
             + " & ".join(rf"\textbf{{{CITY_LABEL[s]}}}" for s in PILOTS[:2]),
             cell + "& " + " & ".join(rf"\textbf{{{CITY_LABEL[s]}}}" for s in PILOTS[2:])
             + r" \\",
             cell + r"\hline"]
    for i, (label, get) in enumerate(rows):
        if i:
            lines.append("")
        lines.append(cell + label)
        lines.append(cell + "& " + " & ".join(get(dyn[s]) for s in PILOTS) + r" \\")
    lines.append(ind + r"\end{tabular}")
    return lines


# --------------------------------------------------------------------------- #

def write(dest: Path, lines: list[str]) -> None:
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\r\n")
    print(f"written : {dest}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=os.environ.get("PILOT_TREE"),
                    help="pilot output tree, one folder per city (default: env PILOT_TREE)")
    ap.add_argument("--calibration-csv", default=None,
                    help="calibration_arms_summary.csv (default: "
                         "urban-heat/validation/outputs/calibration_arms_summary.csv)")
    ap.add_argument("--out", default=None,
                    help="output directory (default: visual_items/pilot_cities/"
                         "workflow_tables/out/)")
    ap.add_argument("--tables", nargs="+", choices=TABLES, default=list(TABLES),
                    help="which fragments to write (default: all three)")
    args = ap.parse_args(argv)

    root = find_repo_root()
    cfgs = load_configs(root)
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    if "calibration_arms" in args.tables:
        csv = Path(args.calibration_csv) if args.calibration_csv else \
            root / "validation" / "outputs" / "calibration_arms_summary.csv"
        print(f"calibration arms : {csv}")
        write(out_dir / "calibration_arms.tex",
              header("tab:calibration_arms",
                     ["calibration_arms_summary.csv (per_capita rows), written by "
                      "urban-heat/validation/plot_calibration_arms.py"])
              + calibration_arms(csv))

    if "city_results" in args.tables:
        if not args.tree:
            raise SystemExit("city_results needs the pilot output tree: --tree or PILOT_TREE")
        tree = Path(args.tree)
        print(f"pilot tree       : {tree}")
        nums = {s: city_numbers(tree, s, cfgs[s], root) for s in PILOTS}
        for s in PILOTS:
            r = nums[s]
            print(f"  {s:<11} dT2M {r['dt']:.4f} degC from {Path(r['dt_source']).name}; "
                  "monthly " + " ".join(f"{v:.3f}" for v in r["dt_monthly"]))
        write(out_dir / "city_results.tex",
              header("tab:city_results",
                     ["pilot output tree, per city: interim/annual_heat_deaths_generic_"
                      "<city>_<main family>.csv, tables/<city>_cea_summary.csv,",
                      "tables/<city>_ews_parameters.json, and the 2050 monthly T2M "
                      "increments (NB02 delta table, else interim/hazard_events_T2M_daily_"
                      "<city>.csv)"])
              + city_results(nums))

    if "vulnerability_params_city" in args.tables:
        write(out_dir / "vulnerability_params_city.tex",
              header("tab:vulnerability_params_city",
                     ["urban-heat/configs/{" + ",".join(PILOTS) + "}.yml, "
                      "vulnerability.dynamic"])
              + vulnerability_params(cfgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
