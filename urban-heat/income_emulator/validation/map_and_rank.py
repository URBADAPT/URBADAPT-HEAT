"""Verification view of a deploy run: choropleth + ranked poorest/richest table.

Joins a deploy_predict.py output (subcity_code, p_inc) to its boundary and renders a
within-city income-rank map plus the top/bottom predicted units, for eyeballing the
ranking against known geography.

    cd income_emulator
    python validation/map_and_rank.py --pred validation/results/deploy_berlin.csv \
        --boundary data/DE_Berlin_ortsteile.gpkg --layer berlin_ortsteile --title Berlin \
        --out validation/results/berlin_p_inc_map.png
"""
from __future__ import annotations
import argparse
import warnings

warnings.filterwarnings("ignore")


def main():
    import pandas as pd
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--boundary", required=True)
    ap.add_argument("--layer", default=None)
    ap.add_argument("--key", default="boundary_code")
    ap.add_argument("--title", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()

    pred = pd.read_csv(a.pred, dtype={"subcity_code": str})
    g = gpd.read_file(a.boundary, layer=a.layer)
    g = g.merge(pred, left_on=a.key, right_on="subcity_code").to_crs(3035)

    s = pred.sort_values("p_inc")
    print(f"=== {a.title}: predicted POOREST {a.top} ===")
    print(s.head(a.top)[["subcity_code", "p_inc"]].to_string(index=False))
    print(f"=== {a.title}: predicted RICHEST {a.top} ===")
    print(s.tail(a.top)[["subcity_code", "p_inc"]].to_string(index=False))

    fig, ax = plt.subplots(figsize=(11, 10))
    g.plot(column="p_inc", cmap="RdYlGn", legend=True, edgecolor="white", linewidth=0.3, ax=ax,
           legend_kwds={"label": "Predicted within-city income percentile (p_inc)", "shrink": 0.6})
    for _, r in g.iterrows():
        if r.p_inc > 0.92 or r.p_inc < 0.08:
            ax.annotate(str(r[a.key]).split("-")[0], (r.geometry.centroid.x, r.geometry.centroid.y),
                        fontsize=6, ha="center")
    ax.set_title(f"{a.title} - emulated within-city income rank (red=poorest, green=richest)", fontsize=12)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(a.out, dpi=140, bbox_inches="tight")
    print(f"saved {a.out}")


if __name__ == "__main__":
    main()
