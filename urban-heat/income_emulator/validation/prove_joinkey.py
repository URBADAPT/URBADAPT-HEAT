"""Demonstrate the emulator-mode zone-join key bug and its fix.

NB05 (pre-fix) keyed the emulator income with to_cap5() regardless of the zones'
match_by. For a name-keyed city (e.g. Copenhagen districts) to_cap5(name) -> NaN,
so every zone silently fell back to the city mean (flat income, no AC gradient).
cityheat.income_source.zone_key routes by match_by (name -> norm_name), recovering
the join. Run against the committed emulator predictions for a labelled name-keyed city.

    cd income_emulator
    python validation/prove_joinkey.py --city Copenhagen --boundary <DK districts.gpkg>
"""
from __future__ import annotations
import argparse
import os
import sys
import warnings

import pandas as pd

warnings.filterwarnings("ignore")
# cityheat lives at <repo>/urban-heat/cityheat; this file is <repo>/urban-heat/income_emulator/validation
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from cityheat.income_source import to_cap5, zone_key   # noqa: E402


def main():
    import geopandas as gpd
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Copenhagen")
    ap.add_argument("--match-by", default="name")
    ap.add_argument("--pred", default="results/income_index_predictions.csv")
    ap.add_argument("--boundary", required=True, help="boundary whose key is the zone identity")
    ap.add_argument("--boundary-key", default="boundary_name")
    a = ap.parse_args()

    pred = pd.read_csv(a.pred, dtype={"subcity_code": str})
    sub = pred[pred.city == a.city]
    zb = gpd.read_file(a.boundary)
    zone_keys = set(zone_key(zb[a.boundary_key], a.match_by))   # NB05 zone_code under match_by

    broken = sub.subcity_code.map(to_cap5)                      # pre-fix emulator-mode key
    fixed = zone_key(sub.subcity_code, a.match_by)              # cityheat fix
    print(f"{a.city} (match_by={a.match_by}): {len(sub)} emulator rows, {len(zone_keys)} zones")
    print(f"  PRE-FIX  to_cap5(): non-NaN keys = {int(broken.notna().sum())}/{len(sub)} | "
          f"income & zone match = {len(set(broken.dropna()) & zone_keys)}/{len(zone_keys)}  (=> city-mean fill)")
    print(f"  FIXED    zone_key(): income & zone match = {len(set(fixed) & zone_keys)}/{len(zone_keys)}")


if __name__ == "__main__":
    main()
