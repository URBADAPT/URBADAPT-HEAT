"""
Prepare the GVI projections table for URBADAPT dynamic vulnerability inputs.

This converts the official GVIP database released with:
Huisman et al. (2025), "Projections of climate change vulnerability along the
Shared Socioeconomic Pathways 2020-2100", Scientific Data.

Expected source columns from the official CSV:
    iso_code, country, year, ssp, pgvi, ...

Output columns expected by URBADAPT:
    iso3, ssp, year, gvi
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the official GVIP CSV file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to the prepared CSV file.",
    )
    parser.add_argument(
        "--iso3",
        action="append",
        default=[],
        help="ISO3 country code to keep. Repeatable. Defaults to ITA/GRC/PRT/DNK if omitted.",
    )
    parser.add_argument(
        "--max-year",
        type=int,
        default=2100,
        help="Maximum projection year to keep. Default: 2100.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    df = pd.read_csv(args.input)

    required = {"iso_code", "year", "ssp", "pgvi"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required source columns: {sorted(missing)}")

    keep_iso3 = [x.upper() for x in (args.iso3 or ["ITA", "GRC", "PRT", "DNK"])]

    ssp_map = {
        1: "SSP1",
        2: "SSP2",
        3: "SSP3",
        "1": "SSP1",
        "2": "SSP2",
        "3": "SSP3",
        "SSP1": "SSP1",
        "SSP2": "SSP2",
        "SSP3": "SSP3",
    }

    out = df.copy()
    out["iso3"] = out["iso_code"].astype(str).str.upper()
    out["year"] = out["year"].astype(int)
    out["ssp"] = out["ssp"].map(ssp_map)
    out["gvi"] = out["pgvi"].astype(float)

    out = out[
        out["iso3"].isin(keep_iso3)
        & out["ssp"].notna()
        & (out["year"] <= int(args.max_year))
    ].copy()

    out = out[["iso3", "ssp", "year", "gvi"]].drop_duplicates()
    out = out.sort_values(["iso3", "ssp", "year"]).reset_index(drop=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)

    print(f"Saved {len(out):,} rows to {args.output}")
    print(f"ISO3: {sorted(out['iso3'].unique())}")
    print(f"SSP: {sorted(out['ssp'].unique())}")
    print(f"Years: {int(out['year'].min())} -> {int(out['year'].max())}")


if __name__ == "__main__":
    main()
