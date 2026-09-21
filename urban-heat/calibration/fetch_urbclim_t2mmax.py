"""Download the UrbClim T2M daily-MAXIMUM NetCDFs from PROVIDE/VITO for all configured
cities -- a faithful mirror of NB01's T2M daily-MEAN download, pointing at the `_max`
product instead of `_mean`.

Why: `calibration/gen_gvi_reduction.py` (the trees electricity co-benefit) needs each
city's JJA mean daily-MAX 2 m temperature (Falchetta 2026 Fig-5 x-axis). The pipeline
itself only fetches the daily-MEAN (UrbClimT2Mmean); daily-mean is ~5-7 C lower and
would collapse the co-benefit. PROVIDE publishes `T2M_year_daily_max_YYYY.nc` in the
SAME `compressed_daily/<City>/` folder as the mean (verified 2026-08), so this just
fetches it into a sibling `UrbClim/UrbClimT2Mmax/` dir, then run
`gen_city_summer_tmax.py` (which prefers the max product) -> gen_gvi_reduction.py ->
apply_calibration.py.

This is a one-off calibration fetch (NOT part of the per-run NB01), so it lives here in
calibration/ and reads each city's `climate.urbclim_api` (t2m_url, years) + base_dir.

Usage:
  python calibration/fetch_urbclim_t2mmax.py                       # all configured cities
  python calibration/fetch_urbclim_t2mmax.py --cities athens berlin # subset
  python calibration/fetch_urbclim_t2mmax.py --dry-run              # list only, no download
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent      # urban-heat/
CONFIGS = ROOT / "configs"


def fetch_city(slug: str, years_override, dry_run: bool) -> str:
    cfgp = CONFIGS / f"{slug}.yml"
    if not cfgp.exists():
        return f"{slug:14} NO_CONFIG"
    cfg = yaml.safe_load(open(cfgp)) or {}
    api = ((cfg.get("climate", {}) or {}).get("urbclim_api", {}) or {})
    if not api.get("enabled", False):
        return f"{slug:14} api disabled"
    url = api.get("t2m_url")
    if not url:
        return f"{slug:14} no t2m_url"
    years = years_override or [int(y) for y in api.get("years", range(2008, 2018))]
    base_dir = ROOT / str(cfg.get("base_dir", f"data/{slug}"))
    # sibling of the mean dir: UrbClim/UrbClimT2Mmean -> UrbClim/UrbClimT2Mmax
    mean_rel = str(api.get("local_dir", "UrbClim/UrbClimT2Mmean"))
    local_dir = (base_dir / mean_rel.replace("T2Mmean", "T2Mmax")).resolve()

    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as ex:  # noqa: BLE001
        return f"{slug:14} LISTING FAILED: {ex}"
    available = set(re.findall(r'href="([^"]+\.nc)"', r.text))

    local_dir.mkdir(parents=True, exist_ok=True)
    got = have = miss = fail = 0
    for y in years:
        fname = f"T2M_year_daily_max_{y}.nc"
        dest = local_dir / fname
        if dest.exists() and dest.stat().st_size > 0:
            have += 1
            continue
        if fname not in available:
            miss += 1
            continue
        if dry_run:
            got += 1
            continue
        try:
            with requests.get(url.rstrip("/") + "/" + fname, stream=True, timeout=1200) as resp:
                resp.raise_for_status()
                tmp = dest.with_suffix(".nc.part")
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=2 ** 20):
                        if chunk:
                            f.write(chunk)
                tmp.replace(dest)
            got += 1
        except Exception as ex:  # noqa: BLE001
            fail += 1
            print(f"      {slug}/{fname}: FAILED {ex}", file=sys.stderr)
    verb = "would fetch" if dry_run else "downloaded"
    return f"{slug:14} {verb} {got:2d}, had {have:2d}, not-on-server {miss:2d}, failed {fail:2d}  -> {local_dir}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cities", nargs="*", default=None, help="slugs (default: all configs/*.yml)")
    ap.add_argument("--years", nargs="*", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="check the server listing without downloading")
    args = ap.parse_args()
    cities = args.cities or sorted(p.stem for p in CONFIGS.glob("*.yml"))
    print(f"UrbClim T2M daily-MAX fetch {'(dry-run) ' if args.dry_run else ''}for {len(cities)} cities:")
    for slug in cities:
        print("  " + fetch_city(slug, args.years, args.dry_run))
    print("\nNext: python calibration/gen_city_summer_tmax.py  (fills tmax_jja_c from the max product)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
