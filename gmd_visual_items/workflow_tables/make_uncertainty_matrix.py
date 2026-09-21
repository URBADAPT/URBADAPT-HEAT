"""CLI: emit the URBADAPT-HEAT uncertainty-quantification matrix (appendix Table B4).

The economic and EWS parameters in this table are city-specific and live in
``urban-heat/configs/<slug>.yml``. Maintaining them by hand let the table drift
badly out of step with the model: before this generator existed it still carried
a legacy single baseline (AC CAPEX 500 EUR, tariff 0.25 EUR/kWh, AC lifetime
10 yr, tree CAPEX 210 EUR, tree O&M 27 EUR) against calibrated city values of
1336-2156 EUR, 0.14-0.30 EUR/kWh, 12 yr, 606-978 EUR and 56-91 EUR; it stated
EWS ramp years as "Rome 3, Lisbon 2, Athens 5" when every config says 3 and the
manuscript says class-specific ramps are not applied; and it omitted Copenhagen
from every per-city footnote.

Values now come from the configs. Rows whose value is identical across the pilot
cities print that value; rows that differ print "city-specific" and carry the
per-city values in the footnote, so a new or changed city cannot be left out.

Usage (from the ``gmd_visual_items/`` directory)::

    python -m workflow_tables.make_uncertainty_matrix
    python -m workflow_tables.make_uncertainty_matrix --out /path/to/overleaf/tables

Sampling ranges that are declared in Notebook 09 rather than in the city configs
(MDD scales, mortality displacement, PAA, cooling-coefficient scale, waste-heat
ratios) are held as literals here and annotated with their source.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PILOTS = ["rome", "athens", "lisbon", "copenhagen"]
CITY_LABEL = {"rome": "Rome", "athens": "Athens",
              "lisbon": "Lisbon", "copenhagen": "Copenhagen"}

CITY_SPECIFIC = "city-specific"


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the ``urban-heat`` directory (mirrors workflow_figures.loader)."""
    p = (start or Path(__file__).resolve()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "configs").is_dir() and cand.name == "urban-heat":
            return cand
        probe = cand / "urban-heat"
        if (probe / "configs").is_dir():
            return probe
    raise SystemExit("could not locate urban-heat/ with a configs/ directory")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def tex_escape_path(dotted: str) -> str:
    """Render a config key path with break opportunities, as ``ac.\\allowbreak ...``."""
    out = dotted.replace("_", r"\_")
    for ch in (".", r"\_"):
        out = out.replace(ch, ch + r"\allowbreak ")
    return out


def num(v) -> str:
    """Format a config number without trailing noise (0.30 -> 0.3, 12.0 -> 12)."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = ("%.6f" % v).rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


def thousands(v) -> str:
    """1498 -> 1{,}498, matching the manuscript's numeric style."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return format(v, ",").replace(",", "{,}")
    return num(v)


def eur(v) -> str:
    r"""Monetary value in the manuscript's form: \euro{}1{,}498."""
    return r"\euro{}" + thousands(v)


class Cfg:
    def __init__(self, root: Path):
        self.data = {}
        for slug in PILOTS:
            path = root / "configs" / f"{slug}.yml"
            if not path.exists():
                raise SystemExit(f"missing config: {path}")
            self.data[slug] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def get(self, slug: str, dotted: str):
        cur = self.data[slug]
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

    def all(self, dotted: str) -> dict:
        return {s: self.get(s, dotted) for s in PILOTS}

    def uniform(self, dotted: str):
        """Return the shared value if every pilot agrees, else None."""
        vals = list(self.all(dotted).values())
        if any(v is None for v in vals):
            return None
        return vals[0] if len(set(map(str, vals))) == 1 else None

    def per_city_note(self, dotted: str, fmt=num) -> str:
        vals = self.all(dotted)
        return "; ".join(f"{CITY_LABEL[s]}: {fmt(vals[s])}" for s in PILOTS)

    def triple_note(self, dotted: str) -> str:
        """Per-city low/central/high triples for an efficacy-style block."""
        parts = []
        for s in PILOTS:
            blk = self.get(s, dotted)
            if not isinstance(blk, dict):
                parts.append(f"{CITY_LABEL[s]}: --")
                continue
            parts.append("%s: %s/%s/%s" % (
                CITY_LABEL[s], num(blk.get("low")), num(blk.get("central")),
                num(blk.get("high"))))
        return "; ".join(parts)

    def triple_uniform(self, dotted: str):
        """If every pilot shares the same low/central/high, return that triple."""
        seen = set()
        trip = None
        for s in PILOTS:
            blk = self.get(s, dotted)
            if not isinstance(blk, dict):
                return None
            trip = (num(blk.get("low")), num(blk.get("central")), num(blk.get("high")))
            seen.add(trip)
        return trip if len(seen) == 1 else None


# --------------------------------------------------------------------------- #
# table construction
# --------------------------------------------------------------------------- #

def build_rows(c: Cfg) -> list:
    """Return a list of ('group', title) and ('row', cells, footnote) items."""
    R: list = []

    def group(title):
        R.append(("group", title))

    def row(label, desc, base, low, high, dist, note=None):
        R.append(("row", [label, desc, base, low, high, dist], note))

    # ---------------------------------------------------------------- hazard
    group("Hazard: additional parameters")
    row("Baseline T2M mode",
        "UrbClim 2008--2017 baseline construction",
        r"climatology\allowbreak\_\allowbreak mean", r"domain\allowbreak\_\allowbreak peak\allowbreak\_\allowbreak day",
        r"warmest\allowbreak\_\allowbreak summer", "Discrete (4 modes)",
        "NB02; " + tex_escape_path("climate.t2m_baseline_mode_options")
        + r"; also pixelwise\allowbreak\_\allowbreak doy\allowbreak\_\allowbreak max (extreme sensitivity)")
    row("Waste-heat LUT case",
        r"Salamanca 2014 LUT: AC penetration $\to$ nighttime dT",
        "central", "low", "high", r"Discrete \{low, central, high\}",
        "All configs " + tex_escape_path("ac.waste_heat.lut_case_options")
        + r"; \citet{Salamanca2014} Table~2")
    row("Waste-heat night-to-daily ratio",
        "Fraction of nighttime dT attributed to the daily mean",
        "0.5", "0.33", "0.67", "Uniform(0.33, 0.67)",
        "All configs " + tex_escape_path("ac.waste_heat.dailymean_from_night_range")
        + "; NB08 CBA")
    row("COP degradation sensitivity",
        "Fractional COP drop per $^\\circ$C of outdoor T rise",
        "0.065", "0.04", "0.09", r"Discrete \{low, central, high\}",
        "All configs "
        + tex_escape_path("ac.waste_heat.cop_degradation.sensitivity_per_C")
        + r"; \citet{kouetal2026} Fig.~2a: 4--9\,\% per $^\circ$C")

    # ------------------------------------------------------- vulnerability
    group("Dynamic vulnerability (projected SVI)")
    k = c.uniform("vulnerability.dynamic.k")
    row("Persistence ($k$)",
        "Spatial persistence of inequality over time; higher $k$ means slower "
        "convergence of vulnerability gaps",
        num(k) if k is not None else CITY_SPECIFIC, "0.55", "0.95",
        "Uniform(0.55, 0.95)",
        tex_escape_path("vulnerability.dynamic.k")
        + "; controls how quickly spatial vulnerability patterns converge "
          "toward the city mean"
        + ("" if k is not None else ". " + c.per_city_note("vulnerability.dynamic.k")))
    phi = c.uniform("vulnerability.dynamic.phi.default_2050")
    row(r"Spatial retention ($\varphi_{2050}$)",
        "Fraction of original spatial variance retained by 2050; lower means "
        "stronger spatial smoothing",
        num(phi) if phi is not None else CITY_SPECIFIC, "0.5", "0.9",
        "Uniform(0.50, 0.90)",
        tex_escape_path("vulnerability.dynamic.phi.default_2050")
        + r"; linearly interpolated 2030$\to$2050"
        + ("" if phi is not None else ". "
           + c.per_city_note("vulnerability.dynamic.phi.default_2050")))

    for comp, key in (("foreign-born", "foreign_born_projection"),
                      ("unemployment", "unemployment_projection")):
        path = f"vulnerability.dynamic.{key}.drmkc_scale"
        v = c.uniform(path)
        lo, hi = (0.02, 0.08) if comp == "foreign-born" else (0.04, 0.16)
        row(f"DRMKC scale ({comp})",
            r"Short-run ($\le$2030) DRMKC trend multiplier for the %s component" % comp,
            num(v) if v is not None else CITY_SPECIFIC, num(lo), num(hi),
            "Uniform(%s, %s)" % (num(lo), num(hi)),
            tex_escape_path(path) + "; JRC DRMKC social-indicator trends"
            + ("" if v is not None else ". " + c.per_city_note(path)))

    for comp, key, lo, hi in (("foreign-born", "foreign_born_projection", 0.15, 0.55),
                              ("unemployment", "unemployment_projection", 0.25, 0.75)):
        path = f"vulnerability.dynamic.{key}.gvi_scale"
        v = c.uniform(path)
        row(f"GDL--GVI scale ({comp})",
            r"Long-run ($>$2030) SSP-driven multiplier for the %s component" % comp,
            num(v) if v is not None else CITY_SPECIFIC, num(lo), num(hi),
            "Uniform(%s, %s)" % (num(lo), num(hi)),
            tex_escape_path(path)
            + "; GDL--GVI SSP scenario trajectories (the national socioeconomic "
              "vulnerability index, not the Green View Index)"
            + ("" if v is not None else ". " + c.per_city_note(path)))

    rr = c.uniform("vulnerability.dynamic.thermal_projection.retrofit_rate_per_year")
    row("Retrofit rate",
        "Annual rate of thermal-efficiency improvement of the building stock",
        num(rr) if rr is not None else CITY_SPECIFIC, "0.005", "0.02",
        "Uniform(0.005, 0.020)",
        tex_escape_path("vulnerability.dynamic.thermal_projection.retrofit_rate_per_year")
        + "; reduces the thermal vulnerability component annually")

    # ------------------------------------------------- impact functions (NB09)
    group("Impact functions: continuous parameters")
    for lab, desc in ((r"MDD scale ($<$15)", r"MDD multiplier for age $<$15"),
                      ("MDD scale (15--64)", "MDD multiplier for age 15--64"),
                      ("MDD scale (65+)", "MDD multiplier for age 65+")):
        row(lab, desc, "1", "0.8", "1.2", "Uniform(0.80, 1.20)",
            "NB09 sampling range, not a city-config value")
    row("Mortality displacement", "Fraction of deaths shifted in time",
        "0", "0", "0.3", "Uniform(0.00, 0.30)",
        r"Net mortality reduction $=(1-\text{disp})\times$ MDD; NB09 sampling range")
    row(r"\% pop.\ affected (PAA)",
        "Fraction of the population exposed to the heat hazard",
        "1", "0.8", "1", "Uniform(0.80, 1.00)",
        "CLIMADA fraction of affected exposure, not a population-attributable "
        "fraction; NB09 sampling range")

    # ------------------------------------------------------------------- EWS
    group("Early warning system (EWS) parameters")
    row("EWS interpretation",
        "Marginal (enhance an existing system) versus counterfactual "
        "(deploy from a low baseline)",
        CITY_SPECIFIC, "--", "--", "Discrete: city-dependent",
        "Determines which efficacy block applies. Marginal in Rome and Lisbon; "
        "counterfactual in Athens and Copenhagen")

    for age, key in ((r"$<$15", "<15"), ("15--64", "15-64"), ("65+", "65+")):
        path = f"ews.efficacy_marginal.{key}"
        trip = c.triple_uniform(path)
        base = num(trip[1]) if trip else CITY_SPECIFIC
        lo = num(trip[0]) if trip else "--"
        hi = num(trip[2]) if trip else "--"
        row(f"EWS marginal eff.\\ ({age})",
            f"Marginal efficacy for the {age} age group",
            base, lo, hi, r"Discrete \{low, central, high\}",
            r"ews.\allowbreak efficacy\allowbreak\_\allowbreak marginal, low/central/high. "
            + c.triple_note(path))

    trip = c.triple_uniform("ews.efficacy_counterfactual")
    row("EWS counterfactual eff.",
        "Full-system efficacy (flat, no age differentiation)",
        num(trip[1]) if trip else CITY_SPECIFIC,
        num(trip[0]) if trip else "--", num(trip[2]) if trip else "--",
        r"Discrete \{low, central, high\}",
        r"ews.\allowbreak efficacy\allowbreak\_\allowbreak counterfactual, "
        "low/central/high. " + c.triple_note("ews.efficacy_counterfactual"))

    trip = c.triple_uniform("ews.ac_overlap_factor")
    row("AC--EWS overlap factor",
        "Fraction of AC-prevented deaths that reduces the EWS addressable pool",
        num(trip[1]) if trip else CITY_SPECIFIC,
        num(trip[0]) if trip else "--", num(trip[2]) if trip else "--",
        r"Discrete \{low, central, high\}",
        "Reflects city-specific AC penetration. "
        + c.triple_note("ews.ac_overlap_factor"))

    for age, key in ((r"$<$15", "<15"), ("15--64", "15-64"), ("65+", "65+")):
        path = f"ews.displacement.{key}"
        trip = c.triple_uniform(path)
        row(f"EWS displacement ({age})",
            f"Harvesting fraction for {age}",
            num(trip[1]) if trip else CITY_SPECIFIC,
            num(trip[0]) if trip else "--", num(trip[2]) if trip else "--",
            r"Discrete \{low, central, high\}",
            r"ews.\allowbreak displacement" + (
                "; identical across the pilot cities" if trip
                else ". " + c.triple_note(path)))

    ramp = c.uniform("ews.ramp_years")
    row("EWS ramp years", "Years for the EWS to reach full efficacy",
        num(ramp) if ramp is not None else CITY_SPECIFIC, "--", "--",
        "Sampled in NB09",
        tex_escape_path("ews.ramp_years")
        + (". Identical across the pilot cities: the implementation applies a "
           "single low-efficacy period and does not yet differentiate ramp "
           "duration by maturity class" if ramp is not None
           else ". " + c.per_city_note("ews.ramp_years")))

    setup = c.uniform("ews.capex_setup")
    row("EWS setup CAPEX",
        "One-off deployment expenditure by baseline maturity class",
        eur(setup) if setup is not None else CITY_SPECIFIC,
        "--", "--", "Discrete: by maturity class",
        tex_escape_path("ews.capex_setup") + r" (\euro{}). "
        + c.per_city_note("ews.capex_setup", fmt=eur))

    cm = c.uniform("ews.cost_model")
    row("EWS cost model", "Operating-cost formulation for the EWS",
        str(cm) if cm is not None else CITY_SPECIFIC, "--", "--",
        r"Discrete \{pavanello, chiabai\}",
        tex_escape_path("ews.cost_model")
        + r"; \citet{Pavanello2025Dec} \$0.014/capita/day, Chiabai "
          r"\citet{Chiabai2018} \euro{}6\,200--14\,000/day")

    # ------------------------------------------------------------------ trees
    group("Tree / vegetation cooling parameters")
    row("Cooling coeff.\\ scale",
        r"Multiplier on the GVI$\to$dT regression coefficients",
        "1", "0.5", "1.5", "Uniform(0.50, 1.50)",
        r"NB07; $\pm$50\,\% brackets regression uncertainty "
        r"(standard errors available per LCZ and month)")
    cu = c.uniform("trees.cap_uplift_0_1")
    row("Cap uplift (GVI)", "Maximum GVI index-point uplift from tree planting",
        num(cu) if cu is not None else CITY_SPECIFIC, "0.06", "0.2",
        "Uniform(0.06, 0.20)",
        tex_escape_path("trees.cap_uplift_0_1")
        + "; depends on policy ambition and available space")
    tr = c.uniform("trees.ramp_years")
    row("Tree ramp years", "Years for a tree to reach full cooling maturity",
        num(tr) if tr is not None else CITY_SPECIFIC, "8", "15", "Discrete",
        tex_escape_path("trees.ramp_years") + "; reflects Mediterranean tree growth")
    row("Tree start age", "Age of the tree at planting",
        "5 yr", "0 yr", "5 yr", "Discrete (2 levels)",
        "NB08 sensitivity: age 0 (seedling) versus age 5 (pre-grown)")
    tl = c.uniform("trees.lifetime_years")
    row("Tree lifetime", "Asset life over which tree flows are evaluated",
        num(tl) + " yr" if tl is not None else CITY_SPECIFIC, "--", "--",
        "Fixed", tex_escape_path("trees.lifetime_years"))

    # ------------------------------------------------------------------- CBA
    group("CBA / economic parameters (post-impact)")
    dr = c.uniform("cba.discount_rate")
    row("Discount rate", "Real social discount rate",
        num(dr) if dr is not None else CITY_SPECIFIC, "0.01", "0.07",
        r"Reported at 1, 3, 5, 7\,\%",
        tex_escape_path("cba.discount_rate")
        + r"; the CBA reports discount-rate sensitivity at 1, 3, 5 and 7\,\%")

    ac_capex = c.uniform("ac.capex_per_user")
    row("AC CAPEX/user", "AC installation cost per additional user",
        eur(ac_capex) if ac_capex is not None else CITY_SPECIFIC,
        r"$\times$0.8", r"$\times$1.2", "Discrete (3 multipliers)",
        tex_escape_path("ac.capex_per_user") + r" (\euro{}). "
        + c.per_city_note("ac.capex_per_user", fmt=eur)
        + ". Sampled as a multiplier on the calibrated city value")

    ac_tariff = c.uniform("ac.tariff_eur_per_kwh")
    row("AC tariff", "Electricity price for AC operation",
        num(ac_tariff) if ac_tariff is not None else CITY_SPECIFIC,
        "--", "--", "Sampled in NB09",
        tex_escape_path("ac.tariff_eur_per_kwh") + r" (\euro{}\,kWh$^{-1}$). "
        + c.per_city_note("ac.tariff_eur_per_kwh"))

    ac_life = c.uniform("ac.lifetime_years")
    row("AC lifetime", "Replacement cycle for an AC unit",
        num(ac_life) + " yr" if ac_life is not None else CITY_SPECIFIC,
        "9 yr", "16 yr", "Discrete (3 levels)",
        tex_escape_path("ac.lifetime_years")
        + "; identical across the pilot cities, sampled over 9--16\\,yr")

    t_capex = c.uniform("trees.capex_per_tree_eur")
    row("Tree CAPEX/tree", "Capital cost per tree planted",
        eur(t_capex) if t_capex is not None else CITY_SPECIFIC,
        r"$\times$0.8", r"$\times$1.2", "Discrete (3 multipliers)",
        tex_escape_path("trees.capex_per_tree_eur") + r" (\euro{}). "
        + c.per_city_note("trees.capex_per_tree_eur", fmt=eur)
        + ". Sampled as a multiplier on the calibrated city value")

    t_om = c.uniform("trees.om_per_tree_per_year_eur")
    row("Tree O\\&M/tree/yr", "Annual maintenance cost per tree",
        eur(t_om) if t_om is not None else CITY_SPECIFIC,
        r"$\times$1", r"$\times$5", "Discrete (base or 5$\\times$)",
        tex_escape_path("trees.om_per_tree_per_year_eur") + r" (\euro{}). "
        + c.per_city_note("trees.om_per_tree_per_year_eur", fmt=eur)
        + ". NB08 tests a 5$\\times$ O\\&M scenario")

    t_idx = c.uniform("trees.capex_per_index_pt_eur")
    row("Tree CAPEX/GVI point",
        "Capital cost per unit of summed regional GVI uplift",
        eur(t_idx) if t_idx is not None else CITY_SPECIFIC,
        "--", "--", "Fixed",
        tex_escape_path("trees.capex_per_index_pt_eur") + r" (\euro{})")

    return R


CAPTION = (
    r"URBADAPT-HEAT uncertainty quantification matrix: principal continuous and "
    r"discrete parameters. Baseline values are the reference configuration and "
    r"Low/High delimit the sampled range; where a multiplier is shown, the "
    r"coordinate is sampled relative to the calibrated city-specific value. "
    r"Entries reading \enquote*{city-specific} differ among the pilot "
    r"cities and are listed per city in the corresponding footnote. This table "
    r"reports the principal sampled parameters rather than the complete "
    r"declaration: the canonical daily-mean track declares 57 input coordinates, "
    r"some of which are conditional on other sampled choices and inactive for "
    r"particular branches, as described in Sect.~\ref{sec:uncertainty}. "
    r"Generated from the city configuration files by "
    r"\texttt{workflow\_tables.make\_uncertainty\_matrix}; do not edit by hand."
)

HEAD = r"""%% ==========================================================================
%% GENERATED FILE -- do not edit by hand.
%% Produced by gmd_visual_items/workflow_tables/make_uncertainty_matrix.py
%% from urban-heat/configs/{rome,athens,lisbon,copenhagen}.yml
%% Regenerate with:
%%     python -m workflow_tables.make_uncertainty_matrix --out <tables dir>
%% Requires in the preamble: booktabs, array, longtable, xcolor (table), eurosym.
%% Column type L is taken from the host preamble when defined.
%% ==========================================================================
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.18}
\emergencystretch=5em
\small

\begin{longtable}{@{}L{0.150\textwidth} L{0.258\textwidth} L{0.135\textwidth} L{0.093\textwidth} L{0.093\textwidth} L{0.143\textwidth}@{}}
\caption{CAPTION_HERE}\label{tab:uq}\\
\toprule
\textbf{Parameter} & \textbf{Description} & \textbf{Baseline} & \textbf{Low} & \textbf{High} & \textbf{Distribution} \\
\midrule
\endfirsthead
\multicolumn{6}{@{}l@{}}{\footnotesize\itshape Table \thetable\ (continued)}\\
\toprule
\textbf{Parameter} & \textbf{Description} & \textbf{Baseline} & \textbf{Low} & \textbf{High} & \textbf{Distribution} \\
\midrule
\endhead
\midrule
\multicolumn{6}{r@{}}{\footnotesize\itshape continued on next page}\\
\endfoot
\bottomrule
\endlastfoot
"""


def render(rows: list) -> str:
    out = [HEAD.replace("CAPTION_HERE", CAPTION)]
    first_group = True
    for item in rows:
        if item[0] == "group":
            if not first_group:
                out.append(r"\addlinespace[2pt]")
            first_group = False
            out.append(r"\rowcolor{gray!12}\multicolumn{6}{@{}l@{}}{\textbf{%s}}\\*"
                       % item[1])
            continue
        _, cells, note = item
        label = cells[0]
        if note:
            label = label + r"\footnote{%s}" % note
        out.append(" & ".join([label] + cells[1:]) + r" \\")
    out.append(r"\addlinespace[2pt]")
    out.append(r"\end{longtable}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None,
                    help="output directory (default: gmd_visual_items/)")
    ap.add_argument("--name", default="uncertainty_matrix.tex",
                    help="output filename")
    args = ap.parse_args(argv)

    root = find_repo_root()
    cfg = Cfg(root)
    rows = build_rows(cfg)

    out_dir = Path(args.out) if args.out else root.parent / "gmd_visual_items"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / args.name
    dest.write_text(render(rows), encoding="utf-8", newline="\r\n")

    n_rows = sum(1 for r in rows if r[0] == "row")
    n_groups = sum(1 for r in rows if r[0] == "group")
    n_city = sum(1 for r in rows if r[0] == "row" and CITY_SPECIFIC in r[1][2])
    print(f"configs : {root / 'configs'}")
    print(f"cities  : {', '.join(PILOTS)}")
    print(f"rows    : {n_rows} in {n_groups} groups ({n_city} city-specific)")
    print(f"written : {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
