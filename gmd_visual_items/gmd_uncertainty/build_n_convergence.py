"""Write GMD `tab:n_convergence` from the verified four-pilot NB09 archive.

    python gmd_visual_items/gmd_uncertainty/build_n_convergence.py --archive /path/to/export.tgz --out-dir /path/to/tables
"""

import argparse
import tarfile
from pathlib import Path

import numpy as np

from archive_io import load_samples, verify_checksum

N128 = "n128_seed42_v3_616fbc2d5131"          # headline Masselot N=128 (reused)
N64 = "gmd4_n64_seed42_v3_616fbc2d5131"        # Masselot N=64 (new)
CITIES = ["athens", "rome", "lisbon", "copenhagen"]

def deaths(samples, city, campaign):
    a = samples["annual_deaths"].to_numpy(dtype=float)
    if not np.isfinite(a).all():
        raise ValueError(f"{city}/{campaign}: annual_deaths contains non-finite values")
    return a


def q(a, p):
    return float(np.percentile(a, p))


def fmt(x: float, city: str) -> str:
    # The manuscript displays Copenhagen to one decimal and the other cities
    # to whole deaths, including Lisbon's P5 values (76 and 70).
    decimals = 1 if city == "copenhagen" else 0
    return f"{x:,.{decimals}f}".replace(",", "{,}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path,
                        help="Final four-pilot GMD .tgz, with its .sha256 beside it")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="Directory for the complete n_convergence.tex table")
    args = parser.parse_args()
    archive = args.archive.expanduser()
    verify_checksum(archive)

    # Validate all eight city/campaign inputs before printing any results.
    with tarfile.open(archive, "r:gz") as tar:
        data = {
            city: {
                N128: load_samples(tar, city, N128, 128)[1],
                N64: load_samples(tar, city, N64, 64)[1],
            }
            for city in CITIES
        }

    dm, dp5, dp95 = [], [], []
    rows = []
    for city in CITIES:
        d1 = deaths(data[city][N128], city, N128)
        d0 = deaths(data[city][N64], city, N64)
        m1, m0 = q(d1, 50), q(d0, 50)
        a1, a0 = q(d1, 5), q(d0, 5)
        b1, b0 = q(d1, 95), q(d0, 95)
        dm.append((m0 - m1) / m1 * 100)
        dp5.append(abs(a0 - a1) / a1 * 100)
        dp95.append((b0 - b1) / b1 * 100)
        rows.append(
            f"{city.title():<10} & {fmt(m1, city)} & {fmt(m0, city)} "
            f"& {fmt(a1, city)} & {fmt(a0, city)} "
            f"& {fmt(b1, city)} & {fmt(b0, city)} \\\\"
        )

    median_deltas = ", ".join(
        rf"\({difference:+.1f}\,\%\) in {city.title()}"
        for city, difference in zip(CITIES, dm)
    )
    caption = (
        "Sample-size comparison of the pooled annual heat-attributable mortality "
        "distributions for the four pilot cities: the headline \\(N=128\\) Masselot "
        "Latin-hypercube ensemble and a separately generated \\(N=64\\) ensemble "
        "using the same engine, input definitions, and random seed. Relative to "
        f"\\(N=128\\), the \\(N=64\\) median differs by {median_deltas}. "
        f"P5 estimates differ by at most {max(dp5):.1f}\\,\\%, while "
        f"\\(N=64\\) P95 estimates are {min(dp95):.1f}--{max(dp95):.1f}\\,\\% "
        "higher across the four cities. These differences describe sensitivity to "
        "the two sampling designs; they neither identify the causes of individual "
        "upper-tail draws nor establish convergence. The larger \\(N=128\\) ensemble "
        "is used for the reported uncertainty distributions."
    )
    table = "\n".join([
        r"\begin{table}[H]",
        r"\centering",
        r"\begin{tabular}{l cc cc cc}",
        r"\toprule",
        r" & \multicolumn{2}{c}{Median} & \multicolumn{2}{c}{P5} & \multicolumn{2}{c}{P95} \\",
        r"City & $N{=}128$ & $N{=}64$ & $N{=}128$ & $N{=}64$ & $N{=}128$ & $N{=}64$ \\",
        r"\midrule",
        *rows,
        r"\bottomrule",
        r"\end{tabular}",
        rf"\caption{{{caption}}}",
        r"\label{tab:n_convergence}",
        r"\end{table}",
        "",
    ])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output = args.out_dir / "n_convergence.tex"
    output.write_text(table, encoding="utf-8")
    print(f"saved: {output}")
    for city, difference in zip(CITIES, dm):
        print(f"median {city.title()}: {difference:+.1f}%")
    print(f"P5 max |delta|: {max(dp5):.1f}%")
    print(f"P95 delta range: {min(dp95):.1f}--{max(dp95):.1f}%")


if __name__ == "__main__":
    main()
