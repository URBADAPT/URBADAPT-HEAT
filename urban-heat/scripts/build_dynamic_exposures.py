"""
Refresh exposure_with_vulnerability files so future years use projected vulnerability.

This script keeps the existing exposure scaling logic from NB03, but replaces the
static vulnerability column with the year/scenario-specific SVI saved by
build_projected_vulnerability_layers().
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cityheat.nbsetup import bootstrap
from cityheat.dynamic_vulnerability import refresh_dynamic_exposure_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "city",
        help="City name matching config and outputs folders, e.g. Rome, Athens, Lisbon.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce console output.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = bootstrap(args.city)
    cfg = env["cfg"]
    out_dir = Path(env["OUT"])
    int_dir = Path(env["INT"])
    refresh_dynamic_exposure_files(cfg=cfg, int_dir=int_dir, out_dir=out_dir, verbose=not args.quiet)

    print(f"\nDynamic exposure_with_vulnerability files refreshed for {args.city}.")


if __name__ == "__main__":
    main()
