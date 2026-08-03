#!/usr/bin/env python3
"""Standalone baseline-mortality scaling generator.

Ported verbatim from the (deprecated) `notebooks/city_agnostic/March2026/<City>/04_*`
Burke-main NB04 cells 43-45. Produces, per city, the year x age-group future
baseline-mortality scale factors relative to 2020:

    outputs/<slug>/interim/baseline_heat_scaling_<slug>.csv   (default location)

These factors are consumed by `aggregate_masselot_ifs.py` (SCALING_CSVS ->
load_baseline_scaling), which bakes them into the committed Masselot IF JSONs.

The scaling is a demographic (impact-function-agnostic) baseline-mortality trend
computed from WCDE / Wittgenstein ASSR (age-specific survival ratio) + POP for a
country/SSP scenario. This script has NO dependency on the March2026 pipeline, so
that pipeline can be deleted while keeping full provenance of the scale factors.

Usage:
    python build_baseline_scaling.py --city rome            # one city
    python build_baseline_scaling.py --city rome athens     # several
    python build_baseline_scaling.py --all                  # all 40
    python build_baseline_scaling.py --city rome --validate  # diff vs existing CSV
Options:
    --scenario SSP2   (default; maps to WCDE scenario id via SCEN_TO_WCDE)
    --out-root PATH   (default: <repo>/urban-heat/outputs)  writes <slug>/interim/...
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent               # .../urban-heat/10288665
URBAN_HEAT = HERE.parent                              # .../urban-heat

SCEN_TO_WCDE = {
    "SSP1": 1, "SSP2": 2, "SSP3": 3, "SSP4": 4, "SSP5": 5,
    "SSP2-ZM": 22, "SSP2-DM": 23,
}

# slug -> WCDE-v3 country_name (from preview_configs `wp_country`, verified 2026-07-09).
CITY_COUNTRY = {
    "amsterdam": "Netherlands", "athens": "Greece", "barcelona": "Spain",
    "berlin": "Germany", "bologna": "Italy", "bratislava": "Slovakia",
    "brussels": "Belgium", "bucharest": "Romania", "budapest": "Hungary",
    "cologne": "Germany", "copenhagen": "Denmark", "dublin": "Ireland",
    "hamburg": "Germany", "helsinki": "Finland", "lisbon": "Portugal",
    "ljubljana": "Slovenia", "lyon": "France", "madrid": "Spain",
    "marseille": "France", "milan": "Italy", "munich": "Germany",
    "nantes": "France", "naples": "Italy", "palermo": "Italy",
    "paris": "France", "porto": "Portugal", "prague": "Czech Republic",
    "riga": "Latvia", "rome": "Italy", "rotterdam": "Netherlands",
    "sevilla": "Spain", "sofia": "Bulgaria", "stockholm": "Sweden",
    "tallinn": "Estonia", "thessaloniki": "Greece", "varna": "Bulgaria",
    "vienna": "Austria", "vilnius": "Lithuania", "warsaw": "Poland",
    "zagreb": "Croatia",
}
PILOTS = ["rome", "athens", "lisbon", "copenhagen"]


def _r_code(country_name: str, scenario_id: int, baseline_out: Path) -> str:
    """Verbatim R body from March2026 Burke NB04 cell 43 (country/scenario/path substituted)."""
    return f"""
if (!requireNamespace("wcde", quietly = TRUE)) {{
  install.packages("wcde", repos = "https://cloud.r-project.org")
}}
if (!requireNamespace("dplyr", quietly = TRUE)) {{
  install.packages("dplyr", repos = "https://cloud.r-project.org")
}}
if (!requireNamespace("stringr", quietly = TRUE)) {{
  install.packages("stringr", repos = "https://cloud.r-project.org")
}}
if (!requireNamespace("readr", quietly = TRUE)) {{
  install.packages("readr", repos = "https://cloud.r-project.org")
}}

library(wcde)
library(dplyr)
library(stringr)
library(readr)

country_name <- "{country_name}"
scenario_id  <- {scenario_id}

to_decade <- function(period) {{
  start <- as.integer(sub("-.*$", "", period))
  (start %/% 10) * 10
}}

lower_age <- function(age) {{
  age_clean <- ifelse(
    endsWith(age, "+"),
    substr(age, 1, nchar(age) - 1),
    sub("--.*$", "", age)
  )
  as.integer(age_clean)
}}

# ASSR = age-specific survival ratio, returned by period
assr_df <- get_wcde(
  indicator = "assr",
  scenario = scenario_id,
  country_name = country_name,
  include_scenario_names = TRUE,
  server = "search-available",
  version = "wcde-v3"
) |>
  mutate(
    age = str_replace_all(age, regex("^newborns?$", ignore_case = TRUE), "0--4"),
    age = str_replace_all(age, regex("^100--104$", ignore_case = TRUE), "100+"),
    start_yr = as.integer(str_sub(period, 1, 4)),
    end_yr   = as.integer(str_sub(period, 6, 9)),
    decade   = to_decade(period),
    asmr_5y  = 1 - assr
  ) |>
  filter(start_yr >= 2020, end_yr <= 2100)

# POP = population size in thousands
pop_df <- get_wcde(
  indicator = "pop",
  scenario = scenario_id,
  country_name = country_name,
  pop_age = "all",
  pop_sex = "all",
  include_scenario_names = TRUE,
  server = "search-available",
  version = "wcde-v3"
) |>
  filter(
    year %in% c(2020, 2030, 2040, 2050),
    age != "All",
    sex != "Both"
  ) |>
  mutate(
    age = str_replace_all(age, regex("^newborns?$", ignore_case = TRUE), "0--4"),
    age = str_replace_all(age, regex("^100--104$", ignore_case = TRUE), "100+"),
    decade = year,
    pop = pop * 1000
  ) |>
  group_by(scenario, scenario_name, scenario_abb, name, country_code, age, sex, decade) |>
  summarise(pop = sum(pop, na.rm = TRUE), .groups = "drop")

# Average ASSR within each decade-sex-age cell
assr_dec <- assr_df |>
  group_by(scenario, scenario_name, scenario_abb, name, country_code, age, sex, decade) |>
  summarise(asmr_5y = mean(asmr_5y, na.rm = TRUE), .groups = "drop")

# Join population so we can build population-weighted broad age buckets
joined <- assr_dec |>
  left_join(
    pop_df,
    by = c("scenario", "scenario_name", "scenario_abb", "name", "country_code", "age", "sex", "decade")
  ) |>
  mutate(
    age_lo = lower_age(age),
    age_group = case_when(
      age_lo < 15 ~ "<15",
      age_lo < 65 ~ "15-64",
      TRUE ~ "65+"
    )
  )

# Collapse sex + detailed ages to 3 age buckets using population-weighted means
baseline_df <- joined |>
  group_by(decade, age_group) |>
  summarise(
    pop_bucket = sum(pop, na.rm = TRUE),
    asmr_5y_pw = ifelse(
      pop_bucket > 0,
      sum(asmr_5y * pop, na.rm = TRUE) / pop_bucket,
      NA_real_
    ),
    deaths_per_100k = asmr_5y_pw * 1e5 / 5,
    .groups = "drop"
  ) |>
  filter(decade %in% c(2020, 2030, 2040, 2050)) |>
  transmute(
    year = decade,
    age_group = age_group,
    deaths_per_100k = deaths_per_100k
  ) |>
  arrange(year, age_group)

write_csv(baseline_df, "{baseline_out.as_posix()}")
"""


def build_baselinedeaths(country_name: str, scenario_id: int, baseline_out: Path) -> None:
    baseline_out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["Rscript", "--vanilla", "-e", _r_code(country_name, scenario_id, baseline_out)],
        check=True,
    )


def scaling_from_baselinedeaths(baseline_csv: Path) -> pd.DataFrame:
    """NB04 cells 44-45: pivot to year x age_group, divide by the 2020 row."""
    df_b = pd.read_csv(baseline_csv)
    df_b["year"] = df_b["year"].astype(int)
    df_b["deaths_per_100k"] = pd.to_numeric(df_b["deaths_per_100k"], errors="coerce")
    df_b["age_group"] = df_b["age_group"].astype(str).str.strip()
    baseline_mort = (
        df_b.pivot(index="year", columns="age_group", values="deaths_per_100k").sort_index()
    )
    ref_year = 2020 if 2020 in baseline_mort.index else baseline_mort.index.min()
    return baseline_mort / baseline_mort.loc[ref_year]


def build_city(slug: str, scenario: str, out_root: Path, validate: bool) -> pd.DataFrame:
    country = CITY_COUNTRY[slug]
    scenario_id = SCEN_TO_WCDE[scenario]
    int_dir = out_root / slug / "interim"
    int_dir.mkdir(parents=True, exist_ok=True)
    baseline_out = int_dir / f"baselinedeaths_{slug}_{scenario}.csv"

    print(f"[{slug}] WCDE assr+pop  country={country!r}  scenario={scenario}({scenario_id}) ...")
    build_baselinedeaths(country, scenario_id, baseline_out)
    scale = scaling_from_baselinedeaths(baseline_out)

    sf_path = int_dir / f"baseline_heat_scaling_{slug}.csv"
    if validate and sf_path.exists():
        existing = pd.read_csv(sf_path, index_col="year")
        new = scale.reindex(index=existing.index, columns=existing.columns)
        maxdiff = float((new - existing).abs().to_numpy().max())
        status = "MATCH" if maxdiff < 1e-9 else "DIFF"
        print(f"[{slug}] validate vs existing: max abs diff = {maxdiff:.3e}  -> {status}")
        return scale  # do NOT overwrite the pilot file during validation

    scale.to_csv(sf_path)
    print(f"[{slug}] wrote {sf_path}")
    return scale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--city", nargs="*", default=[], help="city slug(s)")
    ap.add_argument("--all", action="store_true", help="all 40 cities")
    ap.add_argument("--scenario", default="SSP2", choices=list(SCEN_TO_WCDE))
    ap.add_argument("--out-root", default=str(URBAN_HEAT / "outputs"))
    ap.add_argument("--validate", action="store_true",
                    help="diff against existing baseline_heat_scaling_<slug>.csv; do not overwrite")
    args = ap.parse_args()

    cities = list(CITY_COUNTRY) if args.all else args.city
    if not cities:
        ap.error("give --city <slug...> or --all")
    unknown = [c for c in cities if c not in CITY_COUNTRY]
    if unknown:
        ap.error(f"unknown slug(s): {unknown}")

    out_root = Path(args.out_root)
    for slug in cities:
        build_city(slug, args.scenario, out_root, args.validate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
