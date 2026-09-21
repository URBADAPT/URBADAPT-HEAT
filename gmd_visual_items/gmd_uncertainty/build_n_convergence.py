"""Rebuild GMD Table `tab:n_convergence` (N=128 vs N=64 annual heat-mortality
distribution for the four pilot cities) from the authoritative NB09 export.

Reads the tgz directly -- no manual extraction needed.
    python build_n_convergence.py
"""
import os, tempfile, tarfile
import numpy as np
import pandas as pd

TGZ = os.path.expanduser(
    "~/Downloads/gmd_nb09_export_2026-09-21/gmd_nb09_four_pilots_complete_v3_616fbc2.tgz"
) # to change if different 
N128 = "n128_seed42_v3_616fbc2d5131"          # headline Masselot N=128 (reused)
N64 = "gmd4_n64_seed42_v3_616fbc2d5131"        # Masselot N=64 (new)
CITIES = ["athens", "rome", "lisbon", "copenhagen"]

_tmp = tempfile.mkdtemp(prefix="gmd_nb09_")
with tarfile.open(TGZ) as t:
    t.extractall(_tmp)
BASE = os.path.join(_tmp, "outputs_variants", "masselot_main_agnostic")


def deaths(city, run):
    f = os.path.join(BASE, city, "tables", "uncertainty_runs", run,
                     f"unc_samples_{city}_improved_fast.csv")
    a = pd.read_csv(f)["annual_deaths"].to_numpy(dtype=float)
    return a[np.isfinite(a)]


def q(a, p):
    return float(np.percentile(a, p))


def fmt(x):
    return (f"{x:,.0f}".replace(",", "{,}")) if x >= 100 else f"{x:.1f}"


print(r"% --- tab:n_convergence body (paste into the manuscript) ---")
dm, dp5, dp95 = [], [], []
for c in CITIES:
    d1, d0 = deaths(c, N128), deaths(c, N64)
    m1, m0 = q(d1, 50), q(d0, 50)
    a1, a0 = q(d1, 5), q(d0, 5)
    b1, b0 = q(d1, 95), q(d0, 95)
    dm.append((m0 - m1) / m1 * 100)
    dp5.append(abs(a0 - a1) / a1 * 100)
    dp95.append((b0 - b1) / b1 * 100)
    print(f"{c.title():<10} & {fmt(m1)} & {fmt(m0)} & {fmt(a1)} & {fmt(a0)} "
          f"& {fmt(b1)} & {fmt(b0)} \\\\")

print("\n% --- caption deltas ---")
for c, x in zip(CITIES, dm):
    print(f"median {c.title()}: {x:+.1f}%")
print(f"P5 max |delta|: {max(dp5):.1f}%")
print(f"P95 delta range: {min(dp95):.1f}--{max(dp95):.1f}%")
