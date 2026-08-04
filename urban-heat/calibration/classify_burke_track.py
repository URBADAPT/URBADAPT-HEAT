"""Classify each city's Burke sensitivity Track-A (standard) vs Track-B (cold-city) from a
Track-A run's deaths, and auto-write the Track-B flag into the cold cities' config files.

Criterion (deaths-based): a city is COLD -> Track-B when its STANDARD Burke sensitivity is
uninformative, i.e. its Burke annual deaths are a tiny fraction of the Masselot-main deaths
(mean over years). ``Burke/Masselot < --threshold`` (default 5%) -> Track-B. Run this on an
all-Track-A run (every city on the standard fixed-20C Burke IF, so the Burke deaths ARE the
standard ones for every city). Track-B cities get the ``extreme_hazard.event_track.
cold_city_if_variant`` block appended before ``zones:`` in their config, so the NEXT run
builds the cold-city IF (Masselot untouched). Idempotent; never removes a flag -- a city
already flagged but now classified Track-A is REPORTED for manual review, not auto-changed.

Reads per city from ``<results-dir>/<slug>/interim/``:
  annual_heat_deaths_generic_<slug>.csv                    (Masselot-main canonical)
  annual_heat_deaths_generic_<slug>_burke_polynomial.csv   (standard Burke)

Usage:
  python calibration/classify_burke_track.py --dry-run          # classify + report, no writes
  python calibration/classify_burke_track.py                    # + write flags into configs
  python calibration/classify_burke_track.py --results-dir results_drive --threshold 0.05
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
ANCHOR = "# TODO: spatial zoning source (shapefile vs OSM admin level).\nzones:"

_BLOCK = """# Burke-sensitivity cold-city (Track-B) IF -- AUTO-CLASSIFIED (deaths-based, classify_burke_track.py):
# standard Burke deaths {burke:.2f}/yr = {ratio:.1%} of Masselot {mass:.1f}/yr (< {thr:.0%} threshold) -> cold city.
# Build the Burke IF at the local warm-season onset T_ref (p90) + cold-city anchors (heuristic
# profile -> GMD Discussion caveat). Burke SENSITIVITY ONLY: enabled / run_extreme_track are left
# unset, so the Masselot-main hazard track is unchanged.
extreme_hazard:
  event_track:
    cold_city_if_variant: true
    t_ref_mode_default: local_p90
    t_ref_percentile_default: 90
    t_ref_baseline_mode_default: climatology_mean
    season:
      start_md: "05-15"
      end_md: "09-30"

"""


def _mean_total(csv: Path):
    """Mean over years of the age-summed annual deaths, or None if absent/empty."""
    if not csv.exists():
        return None
    try:
        d = pd.read_csv(csv)
    except Exception:
        return None
    num = [c for c in d.columns if d[c].dtype.kind in "fi" and c.lower() != "year"]
    if not num or d.empty:
        return None
    return float(d[num].sum(axis=1).mean())


def _find_dir(results_dir: Path, slug: str) -> Path | None:
    for cand in (results_dir / slug / "interim",
                 results_dir / slug.title() / "interim",
                 results_dir / slug, results_dir / slug.title()):
        if cand.exists():
            return cand
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default=str(ROOT / "outputs_variants" / "masselot_main_agnostic"))
    ap.add_argument("--threshold", type=float, default=0.05, help="Burke/Masselot ratio below which -> Track-B")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rdir = Path(args.results_dir)

    rows, n_written, n_b = [], 0, 0
    for cfgp in sorted(CONFIGS.glob("*.yml")):
        slug = cfgp.stem
        text = cfgp.read_text()
        already = "cold_city_if_variant" in text
        idir = _find_dir(rdir, slug)
        mass = _mean_total(idir / f"annual_heat_deaths_generic_{slug}.csv") if idir else None
        burke = _mean_total(idir / f"annual_heat_deaths_generic_{slug}_burke_polynomial.csv") if idir else None
        if mass is None or burke is None:
            rows.append((slug, mass, burke, None, "?", "NO_DEATHS_DATA" + (" (flagged)" if already else "")))
            continue
        if (ROOT / "burke_if_reference" / slug).exists():
            # e.g. Copenhagen: its run uses the committed verbatim cold-city reference (non-zero
            # Burke by design), so the deaths-criterion can't judge it -> keep Track-B, don't touch.
            rows.append((slug, mass, burke, (burke / mass if mass > 0 else None), "B",
                         "committed reference -> Track-B (special; not auto-classified)"))
            n_b += 1
            continue
        ratio = burke / mass if mass > 0 else float("inf")
        cold = ratio < args.threshold
        track = "B" if cold else "A"
        n_b += cold
        act = ""
        if cold and not already:
            if ANCHOR in text:
                if not args.dry_run:
                    cfgp.write_text(text.replace(ANCHOR, _BLOCK.format(burke=burke, ratio=ratio, mass=mass, thr=args.threshold) + ANCHOR, 1))
                    n_written += 1
                act = "would add flag" if args.dry_run else "ADDED flag"
            else:
                act = "!! no zones anchor - add manually"
        elif cold and already:
            act = "already flagged"
        elif (not cold) and already:
            act = "!! FLAGGED but Track-A -> REVIEW"
        rows.append((slug, mass, burke, ratio, track, act))

    print(f"{'city':14}{'Masselot':>10}{'Burke':>9}{'ratio':>8}  {'trk':4}action")
    for slug, m, b, r, t, a in rows:
        ms = f"{m:10.1f}" if m is not None else f"{'--':>10}"
        bs = f"{b:9.2f}" if b is not None else f"{'--':>9}"
        rs = f"{r:7.1%}" if isinstance(r, float) and np.isfinite(r) else f"{'--':>8}"
        print(f"{slug:14}{ms}{bs}{rs}  {t:4}{a}")
    print(f"\n{n_b} cities -> Track-B (threshold {args.threshold:.0%} Burke/Masselot).",
          f"{'(dry-run: no configs written)' if args.dry_run else f'wrote {n_written} config flag(s).'}")
    print("Review any '!! ' rows before the next run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
