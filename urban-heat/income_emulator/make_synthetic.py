#!/usr/bin/env python3
"""Generate a synthetic zone table to smoke-test the pipeline end to end.

Creates several 'cities' with a known latent income process so we can check the
emulator recovers the WITHIN-CITY ordering on held-out cities. One city is left
unlabelled (no income) to mimic the deployment target.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def make(seed=0, n_cities=6, zones_per_city=40, out="data/subdivisions.csv"):
    rng = np.random.RandomState(seed)
    rows = []
    countries = ["IT", "IT", "GR", "DK", "PT", "ES"]
    for ci in range(n_cities):
        city = f"city_{ci}"
        country = countries[ci % len(countries)]
        city_level = rng.uniform(0.8, 1.5)        # arbitrary income scale per city
        for zi in range(zones_per_city):
            pop = rng.lognormal(8.5, 0.5)
            dist = rng.exponential(4.0)           # km to centre
            built_age = rng.uniform(0, 1)
            vol_pc = rng.normal(1.0, 0.25)
            foreign = np.clip(rng.normal(0.12, 0.06), 0, 0.6)
            emp = np.clip(rng.normal(0.62, 0.08), 0.2, 0.95)
            gvi = np.clip(rng.normal(0.25, 0.1), 0, 0.7)
            built_h = np.clip(rng.normal(12, 5), 2, 40)
            # latent within-city income deviation (relative), shared rule across cities
            z = (0.9 * vol_pc - 0.6 * foreign + 1.1 * emp
                 - 0.08 * dist + 0.5 * gvi - 0.3 * built_age + rng.normal(0, 0.15))
            rel = np.exp(0.35 * (z - 0.5))        # relative multiplier
            income = city_level * 22000 * rel     # absolute euro
            rows.append(dict(
                zone_code=f"{city}_z{zi}", city=city, country=country,
                pop_zone=pop, inc_mean=income,
                pop_density=pop / rng.uniform(0.5, 4.0),
                built_fraction=np.clip(rng.normal(0.3, 0.1), 0.02, 0.9),
                volume_per_capita=vol_pc, mean_building_height=built_h,
                built_age_index=built_age,
                smod_urban_centre_share=np.clip(rng.normal(0.6, 0.2), 0, 1),
                share_65plus=np.clip(rng.normal(0.22, 0.05), 0.05, 0.45),
                share_under15=np.clip(rng.normal(0.14, 0.04), 0.03, 0.3),
                employment_rate=emp, foreign_born_share=foreign,
                gvi_mean=gvi, dist_to_centre=dist,
                nightlights_pc=np.clip(rng.normal(1.0, 0.4), 0.05, 4),
                city_population=0.0,
            ))
    df = pd.DataFrame(rows)
    df["city_population"] = df.groupby("city")["pop_zone"].transform("sum")
    # Leave the last city unlabelled to mimic a city with no income data.
    df.loc[df["city"] == f"city_{n_cities-1}", "inc_mean"] = np.nan
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} zones across {n_cities} cities -> {out} "
          f"(city_{n_cities-1} left unlabelled)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/subdivisions.csv")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    make(out=a.out, seed=a.seed)
