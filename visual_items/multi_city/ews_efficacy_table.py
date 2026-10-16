"""Warning-system efficacy actually configured for each city, grouped, plus the bound on the
65+ ordering inversion (counterfactual/intermediate values below the marginal value).

Reads urban-heat/configs/<city>.yml (the `ews:` block) for every configured city and writes
<TAB_DIR>/tab_ews_efficacy.{csv,tex}. The bound re-weights each affected city's central
25-year net avoided deaths for ages 65+ (ews_benefits_25y_<city>.csv in the outputs tree) by
the ratio of the marginal value to the value used; benefits are linear in efficacy within an
age group.

Usage: python ews_efficacy_table.py <outputs tree> [<TAB_DIR>]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIGS = REPO / "urban-heat" / "configs"
AGES = {"65+": "a65", "15-64": "a1564", "<15": "a0"}

# Basis of each counterfactual grade (central value).
POOLED = {0.33: "pooled plan effect for Northern Europe, 33.2\\,\\%",
          0.25: "pooled plan effect for all regions, 25.2\\,\\%",
          0.12: "pooled plan effect for Southern Europe, 11.9\\,\\%"}


def fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0")
    return s + "0" * max(0, 2 - len(s.split(".")[1]))


def efficacies(e: dict) -> dict:
    """Central, low and high efficacy by age, as NB06 assigns them for the city's interpretation."""
    interp = e.get("interpretation", "marginal")
    cf, mg = e.get("efficacy_counterfactual") or {}, e.get("efficacy_marginal") or {}
    out = {}
    for age, tag in AGES.items():
        for k in ("central", "low", "high"):
            m = float(mg[age][k])
            v = float(cf[k]) if interp == "counterfactual" else (
                0.5 * (float(cf[k]) + m) if interp == "intermediate" else m)
            out[f"{tag}_{k}"] = round(v, 4)
    return out


def basis(interp: str, cf: float, marg65: float) -> str:
    if interp == "marginal":
        s = "Judgement below the whole-plan effect measured in Italy"
        return s + "; lower for the most mature system" if marg65 < 0.15 else s
    ref = POOLED.get(cf)
    if interp == "counterfactual":
        return ref[0].upper() + ref[1:] if ref else "Modelling judgement"
    return f"Midpoint of the marginal values and {fmt(cf)} ({ref or 'modelling judgement'})"


def main() -> None:
    tree = Path(sys.argv[1])
    tab_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "tables"
    rows = []
    for f in sorted(CONFIGS.glob("*.yml")):
        e = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("ews") or {}
        if not e.get("efficacy_marginal"):
            continue
        rows.append(dict(city=f.stem, interp=e.get("interpretation", "marginal"),
                         cf=round(float(e["efficacy_counterfactual"]["central"]), 2),
                         marg65=float(e["efficacy_marginal"]["65+"]["central"]), **efficacies(e)))
    df = pd.DataFrame(rows)

    # The marginal value does not enter a counterfactual city's efficacy, so it must not split its group.
    df["marg65_key"] = df["marg65"].where(df["interp"] != "counterfactual", 0.0)
    key = ["interp", "cf", "marg65_key", "a65_central", "a65_low", "a65_high", "a1564_central", "a0_central"]
    g = df.groupby(key)["city"].apply(lambda s: ", ".join(x.title() for x in sorted(s))).reset_index()
    g["marg65"] = g["marg65_key"]
    g["rank"] = g["interp"].map({"marginal": 0, "intermediate": 1, "counterfactual": 2})
    g = g.sort_values(["rank", "a65_central"], ascending=[True, False])
    out = pd.DataFrame({
        "Interpretation": g["interp"].str.capitalize(),
        "Cities": g["city"],
        "65+": [f"{fmt(c)} ({fmt(l)}--{fmt(h)})" for c, l, h in zip(g.a65_central, g.a65_low, g.a65_high)],
        "15-64": g["a1564_central"].map(fmt),
        "<15": g["a0_central"].map(fmt),
        "Basis": [basis(i, c, m) for i, c, m in zip(g["interp"], g["cf"], g["marg65"])],
    })
    tab_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(tab_dir / "tab_ews_efficacy.csv", index=False)
    body = "\n".join(" & ".join(map(str, r)) + " \\\\" for r in out.itertuples(index=False))
    (tab_dir / "tab_ews_efficacy.tex").write_text(
        "\\begin{table}[H]\n\\centering\n\\scriptsize\n"
        "\\caption{Warning-system efficacy assigned to each city: the share of deaths on warning days averted at "
        "full take-up, central value with the sampled range for ages 65+. Marginal: age-specific values for "
        "enhancing a mature epidemiological system; counterfactual: one value for all ages for establishing a "
        "full system; intermediate: the age-wise midpoint of the two. Pooled plan effects from "
        "\\citesupp{urban2025_SI}, whose regional differences are not statistically significant.}\n"
        "\\label{tab:ews_efficacy}\n"
        "\\begin{tabularx}{\\linewidth}{l>{\\raggedright\\arraybackslash}Xllll}\n\\toprule\n"
        "Interpretation & Cities & 65+ & 15--64 & $<$15 & Basis \\\\\n\\midrule\n"
        f"{body}\n\\bottomrule\n\\end{{tabularx}}\n\\end{{table}}\n", encoding="utf-8")
    print(f"[saved] {tab_dir / 'tab_ews_efficacy.tex'} ({len(out)} rows, {len(df)} cities)")

    # Bound on the inversion: 65+ efficacy used below the city's marginal 65+ value.
    inv = df[(df["interp"] != "marginal") & (df["a65_central"] < df["marg65"])]
    for r in inv.itertuples(index=False):
        p = tree / r.city / "tables" / f"ews_benefits_25y_{r.city}.csv"
        if not p.exists():
            print(f"[skip] {r.city}: no {p.name} in the outputs tree")
            continue
        d = pd.read_csv(p)
        d = d[d["scenario"] == "central"]
        net, n65 = d["net_avoided_deaths"].sum(), d["net_65+"].sum()
        add = n65 * (r.marg65 / r.a65_central - 1)
        cost = json.loads((tree / r.city / "tables" / f"{r.city}_cba_summary.json").read_text())["costs"]["ews"]["pv_total"]
        print(f"[caption] {r.city}: 65+ efficacy {fmt(r.a65_central)} < marginal {fmt(r.marg65)}; at the marginal "
              f"value net avoided deaths +{100 * add / net:.1f}%, cost per death EUR {cost / net / 1e3:.0f}k -> "
              f"{cost / (net + add) / 1e3:.0f}k")


if __name__ == "__main__":
    main()
