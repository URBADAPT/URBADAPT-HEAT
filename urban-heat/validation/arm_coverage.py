"""Report which cities each calibration arm actually has on disk.

Arms are pulled from JUNO over a slow link and can be partial. Comparing arms on
different city subsets would be misleading - a half-pulled arm whose cities happen to be
cool would show a different median for that reason alone - so anything that compares arms
has to know the common set first.

Usage:
    python validation/arm_coverage.py
"""

from __future__ import annotations

import glob
import os
import sys

JUNO = r"C:\Users\giaco\OneDrive - IIASA\Documents\juno"
ARMS = {
    "none": r"G:\Il mio Drive\adaptation_infrastructure_database\juno_pull\n128_results",
    "variance": os.path.join(JUNO, "arm_variance"),
    "era5": os.path.join(JUNO, "arm_era5"),
    "both": os.path.join(JUNO, "corrected40"),
}
# A Drive sync leftover that rides along with every pull; it is not a city.
STALE_PREFIX = "bologna.pre_gvi"


def cities_with_results(base: str) -> set[str]:
    out = set()
    for d in glob.glob(os.path.join(base, "*")):
        c = os.path.basename(d)
        if c.startswith(STALE_PREFIX) or not os.path.isdir(d):
            continue
        if os.path.exists(os.path.join(d, "tables",
                                       f"annual_heat_deaths_baseline_current_ac_{c}.csv")):
            out.add(c)
    return out


def main() -> int:
    sets = {}
    for arm, path in ARMS.items():
        base = os.environ.get(f"CALIB_ARM_{arm.upper()}", path)
        if not os.path.isdir(base):
            print(f"{arm:9s} : tree absent ({base})")
            continue
        sets[arm] = cities_with_results(base)
        print(f"{arm:9s} : {len(sets[arm]):2d} cities with a baseline table")
    if not sets:
        print("no arms available")
        return 2

    common = set.intersection(*sets.values())
    widest = max(sets.values(), key=len)
    print(f"\ncommon to all available arms: {len(common)}")
    for arm, s in sets.items():
        missing = sorted(widest - s)
        if missing:
            print(f"  {arm} missing {len(missing)}: "
                  f"{', '.join(missing[:12])}{' ...' if len(missing) > 12 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
