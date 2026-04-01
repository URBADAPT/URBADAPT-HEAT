# summary notebook

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

from .nbsetup import find_repo_root


SUMMARY_YEARS = (2020, 2030, 2040, 2050)
SECTION_STEMS = {
    "1_city_profile": "summary_01_city_profile",
    "2_climate_hazard": "summary_02_climate_hazard",
    "3_vulnerability": "summary_03_vulnerability",
    "4_mortality_if": "summary_04_mortality_if",
    "5_policy_levers": "summary_05_policy_levers",
    "6_cba_dashboard": "summary_06_cba_dashboard",
    "7_uncertainty": "summary_07_uncertainty",
}
POLICY_COLORS = {
    "Trees": "#2f7d4f",
    "AC": "#cf5c36",
    "EWS": "#4263eb",
}
AGE_COLORS = {
    "<15": "#7aa6c2",
    "15-64": "#f28e2b",
    "65+": "#b22222",
}


@dataclass
class SummaryPaths:
    slug: str
    city: str
    root: Path
    out: Path
    int_dir: Path
    tab_dir: Path
    fig_dir: Path
    summary_dir: Path
    uq_dir: Path

    def require(self, label: str, *candidates: Path | None) -> Path:
        path = self.optional(label, *candidates)
        if path is None:
            candidate_str = "\n  ".join(str(p) for p in candidates if p is not None)
            raise FileNotFoundError(
                f"[NB10] {label}: none of the candidates exist:\n  {candidate_str}"
            )
        return path

    def optional(self, label: str, *candidates: Path | None) -> Path | None:
        del label
        for candidate in candidates:
            if candidate is not None and candidate.exists():
                return candidate
        return None

    def latest(self, *candidates: Path | None) -> Path | None:
        existing = [p for p in candidates if p is not None and p.exists()]
        if not existing:
            return None
        return max(existing, key=lambda path: path.stat().st_mtime)

    def latest_glob(self, directory: Path, pattern: str) -> Path | None:
        matches = sorted(directory.glob(pattern))
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)

    def savefig(self, fig: Figure, stem: str) -> Path:
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        path = self.summary_dir / f"{stem}_{self.slug}.png"
        fig.savefig(path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return path


def _build_paths(slug: str, city: str, root: Path) -> SummaryPaths:
    out = root / "outputs" / slug
    return SummaryPaths(
        slug=slug,
        city=city,
        root=root,
        out=out,
        int_dir=out / "interim",
        tab_dir=out / "tables",
        fig_dir=out / "figures",
        summary_dir=out / "figures" / "summary",
        uq_dir=out / "tables" / "uncertainty_improved",
    )


def _scenario_variants(scenario: str | None) -> list[str]:
    if not scenario:
        return []
    base = str(scenario).strip().upper()
    variants: list[str] = []
    for candidate in (base, base.replace("-", "")):
        if candidate and candidate not in variants:
            variants.append(candidate)
    if base.endswith("DM") and not base.endswith("-DM"):
        variants.append(f"{base[:-2]}-DM")
    if base.endswith("-DM"):
        variants.append(base.replace("-DM", "DM"))
    if base.endswith("ZM") and not base.endswith("-ZM"):
        variants.append(f"{base[:-2]}-ZM")
    if base.endswith("-ZM"):
        variants.append(base.replace("-ZM", "ZM"))
    return [v for i, v in enumerate(variants) if v and v not in variants[:i]]


def _placeholder(ax, title: str, text: str) -> None:
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])


def _style_map_axis(ax, title: str, mask: np.ndarray) -> None:
    ax.contour(mask.astype(float), levels=[0.5], colors="black", linewidths=0.7)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal")


def _npz_dict(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as data:
        return {key: data[key] for key in data.files}


def _load_city_mask(sp: SummaryPaths) -> np.ndarray:
    path = sp.require("city mask", sp.int_dir / "city_mask.npz")
    data = _npz_dict(path)
    return data["city_mask"].astype(bool)


def _resolve_population_npz(sp: SummaryPaths) -> Path:
    return sp.require(
        "population raster",
        sp.int_dir / f"pop_on_ref_{sp.slug}_2020.npz",
        sp.int_dir / "pop_on_ref.npz",
        sp.int_dir / f"pop_on_ref_{sp.slug}.npz",
    )


def _resolve_lcz_path(sp: SummaryPaths) -> Path | None:
    return sp.optional(
        "LCZ raster",
        sp.int_dir / f"lcz_on_ref_{sp.slug}.tif",
        sp.int_dir / "lcz_on_ref.tif",
    )


def _resolve_hazard_yearly_csv(sp: SummaryPaths) -> Path:
    return sp.require(
        "hazard yearly CSV",
        sp.int_dir / f"hazard_events_T2M_yearly_{sp.slug}.csv",
    )


def _resolve_hazard_npz(sp: SummaryPaths) -> Path | None:
    return sp.optional(
        "hazard summary NPZ",
        sp.int_dir / f"t2m_hazard_2020_2050_{sp.slug}.npz",
        sp.int_dir / f"t2m_annual_mean_2030_2050_{sp.slug}.npz",
    )


def _resolve_diag_csv(sp: SummaryPaths, scenario: str | None) -> Path | None:
    candidates: list[Path] = []
    for variant in _scenario_variants(scenario):
        candidates.append(
            sp.tab_dir / f"{sp.slug}_vulnerability_projection_diagnostics_{variant.lower()}.csv"
        )
    candidates.append(sp.tab_dir / f"{sp.slug}_vulnerability_projection_diagnostics_ssp2.csv")
    return sp.optional("vulnerability diagnostics CSV", *candidates)


def _resolve_vulnerability_npz(
    sp: SummaryPaths,
    year: int,
    scenario: str | None = None,
) -> Path | None:
    if year in (2020, 2030):
        return sp.optional(
            f"vulnerability {year} NPZ",
            sp.int_dir / f"vulnerability_{sp.slug}_{year}.npz",
            sp.int_dir / f"vulnerability_{sp.slug}.npz" if year == 2020 else None,
        )

    candidates: list[Path] = []
    for variant in _scenario_variants(scenario):
        candidates.append(sp.int_dir / f"vulnerability_{sp.slug}_{variant}_{year}.npz")
    for variant in _scenario_variants("SSP2"):
        candidates.append(sp.int_dir / f"vulnerability_{sp.slug}_{variant}_{year}.npz")
    path = sp.optional(f"vulnerability {year} NPZ", *candidates)
    if path is not None:
        return path
    glob_match = sp.latest_glob(sp.int_dir, f"vulnerability_{sp.slug}_SSP*_{year}.npz")
    return glob_match


def _resolve_if_json(sp: SummaryPaths, family: str = "poly") -> Path | None:
    if family == "poly":
        return sp.optional(
            "polynomial IF JSON",
            sp.int_dir / f"if_curves_by_year_{sp.slug}.json",
            sp.out / f"if_curves_by_year_{sp.slug}.json",
        )
    return sp.optional(
        "powerlaw IF JSON",
        sp.int_dir / f"if_curves_by_year_{sp.slug}_powerlaw.json",
        sp.out / f"if_curves_by_year_{sp.slug}_powerlaw.json",
    )


def _resolve_annual_deaths_csv(sp: SummaryPaths) -> Path:
    return sp.require(
        "annual deaths CSV",
        sp.int_dir / f"annual_heat_deaths_generic_{sp.slug}.csv",
        sp.int_dir / f"annual_heat_deaths_baseline_currentAC_{sp.slug}.csv",
        sp.out / f"annual_heat_deaths_generic_{sp.slug}.csv",
    )


def _resolve_ac_avoided_csv(sp: SummaryPaths) -> Path | None:
    return sp.latest(
        sp.int_dir / f"annual_heat_deaths_climada_avoided_AC_{sp.slug}.csv",
        sp.latest_glob(
            sp.int_dir,
            f"annual_heat_deaths_climada_avoided_AC_{sp.slug}_*.csv",
        ),
    )


def _resolve_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _series_overall(df: pd.DataFrame) -> pd.Series:
    for column in ("overall", "TOTAL", "deaths_annual"):
        if column in df.columns:
            return pd.to_numeric(df[column], errors="coerce")
    age_cols = [col for col in ("<15", "15-64", "65+", "<20", "20-64") if col in df.columns]
    if age_cols:
        return df[age_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    numeric_cols = [col for col in df.select_dtypes(include="number").columns if col != "year"]
    if numeric_cols:
        return df[numeric_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    return pd.Series(np.nan, index=df.index, dtype=float)


def _choose_if_year(*datasets: dict[str, Any]) -> str | None:
    available: set[str] | None = None
    for dataset in datasets:
        if not dataset:
            continue
        years = set(dataset.get("ifs_by_year", {}).keys())
        available = years if available is None else available & years
    if not available:
        return None
    return sorted(available, key=int)[-1]


def _short_policy_label(label: str) -> str:
    if label.startswith("Trees"):
        return "Trees"
    if label.startswith("AC"):
        return "AC"
    if label.startswith("EWS"):
        return "EWS"
    return label


def _parse_cost_stack(cba: dict[str, Any]) -> tuple[list[str], list[list[float]], list[str]]:
    labels = ["Trees", "AC", "EWS"]
    costs = cba.get("costs", {})
    stacks = [
        [
            float(costs.get("trees", {}).get("pv_capex", 0.0)),
            float(costs.get("trees", {}).get("pv_om_base", 0.0)),
            0.0,
        ],
        [
            float(costs.get("ac", {}).get("pv_capex", 0.0)),
            float(costs.get("ac", {}).get("pv_maint", 0.0)),
            float(costs.get("ac", {}).get("pv_elec", 0.0)),
        ],
        [
            float(costs.get("ews", {}).get("pv_total", 0.0)),
            0.0,
            0.0,
        ],
    ]
    component_labels = ["CAPEX", "O&M / maintenance", "Electricity"]
    return labels, stacks, component_labels


def _select_sensitivity_column(df: pd.DataFrame, preferred: Iterable[str]) -> str | None:
    for column in preferred:
        if column in df.columns and df[column].notna().any():
            return column
    for column in df.columns:
        if column not in {"si", "param", "param2"} and df[column].notna().any():
            return column
    return None


def _compute_vulnerability_diagnostics(
    sp: SummaryPaths,
    scenario: str | None,
    mask: np.ndarray,
) -> pd.DataFrame | None:
    records: list[dict[str, Any]] = []
    component_map = {
        "thermal": "thermal",
        "foreign_born": "foreign",
        "unemp": "unemp",
    }
    for year in SUMMARY_YEARS:
        path = _resolve_vulnerability_npz(sp, year, scenario=scenario)
        if path is None:
            continue
        arrays = _npz_dict(path)
        svi = arrays.get("svi")
        if svi is None:
            continue
        svi_vals = svi[mask & np.isfinite(svi)]
        if svi_vals.size == 0:
            continue
        record: dict[str, Any] = {
            "year": year,
            "scenario": "—" if year < 2040 else (scenario or "SSP2"),
            "svi_mean": float(np.nanmean(svi_vals)),
            "svi_median": float(np.nanmedian(svi_vals)),
            "svi_p10": float(np.nanpercentile(svi_vals, 10)),
            "svi_p90": float(np.nanpercentile(svi_vals, 90)),
        }
        for out_name, key in component_map.items():
            arr = arrays.get(key)
            if arr is None:
                continue
            vals = arr[mask & np.isfinite(arr)]
            if vals.size == 0:
                continue
            record[f"{out_name}_mean"] = float(np.nanmean(vals))
            record[f"{out_name}_p10"] = float(np.nanpercentile(vals, 10))
            record[f"{out_name}_p90"] = float(np.nanpercentile(vals, 90))
        records.append(record)
    if not records:
        return None
    return pd.DataFrame(records).sort_values("year").reset_index(drop=True)


def _optional_lcz_array(sp: SummaryPaths) -> np.ndarray | None:
    path = _resolve_lcz_path(sp)
    if path is None:
        return None
    try:
        import rasterio

        with rasterio.open(path) as src:
            return src.read(1).astype(float)
    except Exception as exc:
        warnings.warn(f"[NB10] Could not read LCZ raster for {sp.slug}: {exc}")
        return None


def plot_section_1_city_profile(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    del cfg
    mask = _load_city_mask(sp)
    pop_npz = _npz_dict(_resolve_population_npz(sp))
    pop = pop_npz.get("pop")
    if pop is None:
        raise KeyError(f"[NB10] population NPZ for {sp.slug} does not contain 'pop'")
    pop = pop.astype(float)
    pop = np.where(mask, pop, np.nan)

    lcz = _optional_lcz_array(sp)
    ncols = 3 if lcz is not None else 2
    fig, axes = plt.subplots(1, ncols, figsize=(6 * ncols, 5), constrained_layout=True)
    if ncols == 1:
        axes = np.array([axes])
    fig.suptitle(f"{sp.city} — City Profile", fontsize=14, fontweight="bold")

    mask_img = np.where(mask, 1.0, np.nan)
    axes[0].imshow(mask_img, cmap="Greys", interpolation="nearest")
    _style_map_axis(axes[0], "City footprint", mask)
    pop_total = float(np.nansum(pop))
    axes[0].text(
        0.02,
        0.02,
        f"Population 2020: {pop_total:,.0f}",
        transform=axes[0].transAxes,
        ha="left",
        va="bottom",
        fontsize=9,
        bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
    )

    pop_display = np.where(pop > 0, np.log10(pop), np.nan)
    im_pop = axes[1].imshow(pop_display, cmap="YlOrRd", interpolation="nearest")
    _style_map_axis(axes[1], "Population density 2020", mask)
    fig.colorbar(im_pop, ax=axes[1], shrink=0.8, label="log10(pop / pixel)")

    if lcz is not None:
        lcz = np.where(mask & (lcz > 0), lcz, np.nan)
        im_lcz = axes[2].imshow(lcz, cmap="tab20", vmin=1, vmax=17, interpolation="nearest")
        _style_map_axis(axes[2], "LCZ (optional saved raster)", mask)
        fig.colorbar(im_lcz, ax=axes[2], shrink=0.8, label="LCZ class")

    return [sp.savefig(fig, SECTION_STEMS["1_city_profile"])]


def plot_section_2_climate_hazard(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    del cfg
    hazard_csv = _resolve_hazard_yearly_csv(sp)
    df = pd.read_csv(hazard_csv).sort_values("year")
    mask = _load_city_mask(sp)
    hazard_npz = _resolve_hazard_npz(sp)

    fig = plt.figure(figsize=(18, 6), constrained_layout=True)
    subfigs = fig.subfigures(1, 3, width_ratios=[1.0, 1.0, 1.25])
    fig.suptitle(f"{sp.city} — Climate Hazard", fontsize=14, fontweight="bold")

    ax_trend = subfigs[0].subplots(1, 1)
    ax_metric = subfigs[1].subplots(1, 1)
    map_axes = subfigs[2].subplots(1, 2)

    ax_trend.plot(df["year"], df["citymean_degC"], color="#c2410c", marker="o", linewidth=2)
    ax_trend.set_title("City-mean T2M")
    ax_trend.set_xlabel("Year")
    ax_trend.set_ylabel("degC")
    ax_trend.grid(alpha=0.25)

    if hazard_npz is None:
        _placeholder(ax_metric, "Hot-area metric", "Hazard NPZ not available")
        _placeholder(map_axes[0], "2020 annual mean T2M", "Hazard NPZ not available")
        _placeholder(map_axes[1], "2050 annual mean T2M", "Hazard NPZ not available")
        return [sp.savefig(fig, SECTION_STEMS["2_climate_hazard"])]

    arrays = _npz_dict(hazard_npz)
    years = arrays.get("years")
    data = arrays.get("data")
    if years is None or data is None:
        _placeholder(ax_metric, "Hot-area metric", "Hazard NPZ missing years/data")
        _placeholder(map_axes[0], "2020 annual mean T2M", "Hazard NPZ missing years/data")
        _placeholder(map_axes[1], "2050 annual mean T2M", "Hazard NPZ missing years/data")
        return [sp.savefig(fig, SECTION_STEMS["2_climate_hazard"])]

    year_lookup = {int(year): idx for idx, year in enumerate(years)}

    if 2020 in year_lookup:
        baseline_vals = data[year_lookup[2020]].astype(float)
        baseline_vals = baseline_vals[mask & np.isfinite(baseline_vals)]
        baseline_q90 = float(np.nanpercentile(baseline_vals, 90)) if baseline_vals.size else np.nan
    else:
        baseline_q90 = np.nan

    hot_metric_years: list[int] = []
    hot_metric_share: list[float] = []
    for year in sorted(year_lookup):
        vals = data[year_lookup[year]].astype(float)
        vals = vals[mask & np.isfinite(vals)]
        if vals.size == 0 or not np.isfinite(baseline_q90):
            continue
        hot_metric_years.append(year)
        hot_metric_share.append(float((vals >= baseline_q90).mean()))

    if hot_metric_years:
        ax_metric.plot(
            hot_metric_years,
            hot_metric_share,
            color="#ea580c",
            marker="o",
            linewidth=2,
        )
        ax_metric.axhline(0.10, color="#9ca3af", linestyle="--", linewidth=1.2, label="2020 baseline")
        ax_metric.set_title("Share of city above 2020 P90")
        ax_metric.set_xlabel("Year")
        ax_metric.set_ylabel("Share of city cells")
        ax_metric.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax_metric.grid(alpha=0.25)
        ax_metric.legend(frameon=False, fontsize=8)
    else:
        _placeholder(ax_metric, "Hot-area metric", "Could not compute within-city hazard metric")

    map_years = [year for year in (2020, 2050) if year in year_lookup]
    if len(map_years) == 2:
        map_2020 = np.where(mask, data[year_lookup[2020]].astype(float), np.nan)
        map_2050 = np.where(mask, data[year_lookup[2050]].astype(float), np.nan)
        vmin = float(np.nanmin([np.nanmin(map_2020), np.nanmin(map_2050)]))
        vmax = float(np.nanmax([np.nanmax(map_2020), np.nanmax(map_2050)]))
        im0 = map_axes[0].imshow(map_2020, cmap="YlOrRd", vmin=vmin, vmax=vmax, interpolation="nearest")
        _style_map_axis(map_axes[0], "2020 annual mean T2M", mask)
        im1 = map_axes[1].imshow(map_2050, cmap="YlOrRd", vmin=vmin, vmax=vmax, interpolation="nearest")
        _style_map_axis(map_axes[1], "2050 annual mean T2M", mask)
        subfigs[2].colorbar(im1, ax=map_axes, shrink=0.82, label="degC")
    else:
        missing = ", ".join(str(year) for year in (2020, 2050) if year not in year_lookup)
        _placeholder(map_axes[0], "2020 annual mean T2M", f"Missing years: {missing}")
        _placeholder(map_axes[1], "2050 annual mean T2M", f"Missing years: {missing}")

    return [sp.savefig(fig, SECTION_STEMS["2_climate_hazard"])]


def plot_section_3_vulnerability(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    scenario = str(cfg.get("exp_scenario", "SSP2")).upper()
    mask = _load_city_mask(sp)
    diag_path = _resolve_diag_csv(sp, scenario)
    if diag_path is not None:
        diag = pd.read_csv(diag_path).sort_values("year")
    else:
        diag = _compute_vulnerability_diagnostics(sp, scenario, mask)
    if diag is None or diag.empty:
        warnings.warn(f"[NB10] Vulnerability inputs not found for {sp.slug}.")
        return []

    fig, axes = plt.subplots(1, 3, figsize=(18, 5), constrained_layout=True)
    fig.suptitle(f"{sp.city} — Vulnerability Projection", fontsize=14, fontweight="bold")

    axes[0].fill_between(diag["year"], diag["svi_p10"], diag["svi_p90"], color="#7f1d1d", alpha=0.12)
    axes[0].plot(diag["year"], diag["svi_mean"], color="#991b1b", marker="o", linewidth=2)
    axes[0].set_title("SVI trajectory")
    axes[0].set_xlabel("Year")
    axes[0].set_ylabel("SVI")
    axes[0].set_ylim(0, 1)
    axes[0].grid(alpha=0.25)

    component_specs = [
        ("thermal_mean", "Thermal", "#c2410c"),
        ("foreign_born_mean", "Foreign-born", "#2563eb"),
        ("unemp_mean", "Unemployment", "#2f7d4f"),
    ]
    plotted_components = False
    for column, label, color in component_specs:
        if column in diag.columns:
            plotted_components = True
            axes[1].plot(diag["year"], diag[column], color=color, marker="o", linewidth=2, label=label)
    if plotted_components:
        axes[1].legend(frameon=False, fontsize=8)
        axes[1].set_ylim(0, 1)
        axes[1].grid(alpha=0.25)
    else:
        _placeholder(axes[1], "Component trajectories", "Component fields unavailable")
    axes[1].set_title("SVI components")
    axes[1].set_xlabel("Year")
    axes[1].set_ylabel("Mean value")

    base_path = _resolve_vulnerability_npz(sp, 2020, scenario=scenario)
    future_path = _resolve_vulnerability_npz(sp, 2050, scenario=scenario)
    if base_path is None or future_path is None:
        _placeholder(axes[2], "2050 - 2020 delta SVI", "Spatial vulnerability NPZs unavailable")
    else:
        svi_2020 = _npz_dict(base_path).get("svi")
        svi_2050 = _npz_dict(future_path).get("svi")
        if svi_2020 is None or svi_2050 is None:
            _placeholder(axes[2], "2050 - 2020 delta SVI", "Missing 'svi' arrays")
        else:
            delta = np.where(mask, svi_2050.astype(float) - svi_2020.astype(float), np.nan)
            vmax = max(float(np.nanpercentile(np.abs(delta), 95)), 0.02)
            im = axes[2].imshow(
                delta,
                cmap="RdBu_r",
                vmin=-vmax,
                vmax=vmax,
                interpolation="nearest",
            )
            _style_map_axis(axes[2], f"2050 - 2020 delta SVI ({scenario})", mask)
            fig.colorbar(im, ax=axes[2], shrink=0.8, label="delta SVI")

    return [sp.savefig(fig, SECTION_STEMS["3_vulnerability"])]


def plot_section_4_mortality_if(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    del cfg
    deaths = pd.read_csv(_resolve_annual_deaths_csv(sp)).sort_values("year")
    poly_json = _resolve_if_json(sp, family="poly")
    powerlaw_json = _resolve_if_json(sp, family="powerlaw")
    poly = _resolve_json(poly_json) if poly_json is not None else {}
    powerlaw = _resolve_json(powerlaw_json) if powerlaw_json is not None else {}

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), constrained_layout=True)
    fig.suptitle(f"{sp.city} — Mortality Dose-Response", fontsize=14, fontweight="bold")

    curve_year = _choose_if_year(poly, powerlaw)
    if curve_year is None:
        _placeholder(axes[0], "Impact functions", "IF JSONs unavailable")
    else:
        age_groups = ["<15", "15-64", "65+"]
        for age in age_groups:
            color = AGE_COLORS.get(age, "#555555")
            if poly.get("ifs_by_year", {}).get(curve_year, {}).get(age):
                curve = poly["ifs_by_year"][curve_year][age]
                axes[0].plot(
                    curve.get("intensity", []),
                    curve.get("mdd", []),
                    color=color,
                    linewidth=2,
                    label=f"{age} polynomial",
                )
            if powerlaw.get("ifs_by_year", {}).get(curve_year, {}).get(age):
                curve = powerlaw["ifs_by_year"][curve_year][age]
                axes[0].plot(
                    curve.get("intensity", []),
                    curve.get("mdd", []),
                    color=color,
                    linewidth=2,
                    linestyle="--",
                    label=f"{age} powerlaw",
                )
        axes[0].set_title(f"Impact functions by age ({curve_year})")
        axes[0].set_xlabel("Temperature (degC)")
        axes[0].set_ylabel("MDD")
        axes[0].grid(alpha=0.25)
        axes[0].legend(frameon=False, fontsize=8, ncol=2)

    age_columns = [col for col in ("<15", "15-64", "65+") if col in deaths.columns]
    if age_columns:
        bottom = np.zeros(len(deaths))
        for age in age_columns:
            values = pd.to_numeric(deaths[age], errors="coerce").fillna(0.0).to_numpy()
            axes[1].bar(
                deaths["year"],
                values,
                bottom=bottom,
                color=AGE_COLORS.get(age, "#888888"),
                label=age,
                width=7,
            )
            bottom += values
        axes[1].legend(frameon=False, fontsize=8)
    else:
        total = _series_overall(deaths)
        axes[1].bar(deaths["year"], total, color="#991b1b", width=7)
    axes[1].set_title("Annual baseline deaths")
    axes[1].set_xlabel("Year")
    axes[1].set_ylabel("Deaths / year")
    axes[1].grid(axis="y", alpha=0.25)

    return [sp.savefig(fig, SECTION_STEMS["4_mortality_if"])]


def plot_section_5_policy_levers(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    del cfg
    fig, axes = plt.subplots(1, 3, figsize=(18, 5), constrained_layout=True)
    fig.suptitle(f"{sp.city} — Policy Levers", fontsize=14, fontweight="bold")

    ac_targets_path = sp.optional("AC targets", sp.int_dir / f"ac_city_targets_{sp.slug}.csv")
    ac_avoided_path = _resolve_ac_avoided_csv(sp)
    if ac_targets_path is None or ac_avoided_path is None:
        _placeholder(axes[0], "AC coverage and benefits", "AC targets or avoided-deaths CSV missing")
    else:
        ac_targets = pd.read_csv(ac_targets_path).sort_values("year")
        ac_avoided = pd.read_csv(ac_avoided_path).sort_values("year")
        avoided = _series_overall(ac_avoided)
        ax_right = axes[0].twinx()
        axes[0].plot(
            ac_targets["year"],
            ac_targets["ac_share"],
            color=POLICY_COLORS["AC"],
            marker="o",
            linewidth=2,
        )
        ax_right.bar(ac_avoided["year"], avoided, color="#fed7aa", alpha=0.55, width=6)
        axes[0].set_title("AC penetration and avoided deaths")
        axes[0].set_xlabel("Year")
        axes[0].set_ylabel("AC share")
        axes[0].yaxis.set_major_formatter(PercentFormatter(1.0))
        ax_right.set_ylabel("Avoided deaths / year")
        axes[0].grid(alpha=0.2)

    ews_benefits_path = sp.optional("EWS benefits", sp.tab_dir / f"ews_benefits_25y_{sp.slug}.csv")
    ews_warning_path = sp.optional("EWS warning days", sp.tab_dir / f"{sp.slug}_ews_warning_days.csv")
    if ews_benefits_path is not None:
        ews = pd.read_csv(ews_benefits_path).sort_values("year")
        ax_right = axes[1].twinx()
        axes[1].plot(
            ews["year"],
            ews["net_avoided_deaths"],
            color=POLICY_COLORS["EWS"],
            marker="o",
            linewidth=2,
        )
        ax_right.bar(ews["year"], ews["warning_days"], color="#dbeafe", alpha=0.6, width=0.9)
        axes[1].set_title("EWS warning days and net benefits")
        axes[1].set_xlabel("Year")
        axes[1].set_ylabel("Net avoided deaths / year")
        ax_right.set_ylabel("Warning days")
        axes[1].grid(alpha=0.2)
    elif ews_warning_path is not None:
        warning = pd.read_csv(ews_warning_path).sort_values("year")
        axes[1].bar(warning["year"], warning["warning_days"], color="#dbeafe", alpha=0.9)
        axes[1].set_title("EWS warning days")
        axes[1].set_xlabel("Year")
        axes[1].set_ylabel("Warning days")
        axes[1].grid(axis="y", alpha=0.2)
    else:
        _placeholder(axes[1], "EWS warning days and net benefits", "EWS outputs unavailable")

    trees_path = sp.optional("tree benefits", sp.tab_dir / f"trees_benefits_25y_{sp.slug}.csv")
    if trees_path is None:
        _placeholder(axes[2], "Trees benefits trajectory", "Trees benefits CSV unavailable")
    else:
        trees = pd.read_csv(trees_path).sort_values("year")
        if "trees_only_dynamic" in trees.columns:
            axes[2].plot(
                trees["year"],
                trees["trees_only_dynamic"],
                color=POLICY_COLORS["Trees"],
                marker="o",
                linewidth=2,
                label="Trees only",
            )
        if "trees_on_top_dynamic" in trees.columns:
            axes[2].plot(
                trees["year"],
                trees["trees_on_top_dynamic"],
                color="#1d4ed8",
                marker="o",
                linewidth=2,
                label="Trees on top of AC",
            )
        axes[2].set_title("Trees benefits trajectory")
        axes[2].set_xlabel("Year")
        axes[2].set_ylabel("Avoided deaths / year")
        axes[2].grid(alpha=0.2)
        axes[2].legend(frameon=False, fontsize=8)

    return [sp.savefig(fig, SECTION_STEMS["5_policy_levers"])]


def plot_section_6_cba_dashboard(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    del cfg
    cba_path = sp.require(
        "CBA summary JSON",
        sp.tab_dir / f"{sp.slug}_cba_summary.json",
        sp.out / f"{sp.slug}_cba_summary.json",
    )
    cea_path = sp.require("CEA summary CSV", sp.tab_dir / f"{sp.slug}_cea_summary.csv")
    effectiveness_path = sp.require(
        "policy effectiveness CSV",
        sp.tab_dir / f"{sp.slug}_policy_effectiveness.csv",
    )

    cba = _resolve_json(cba_path)
    cea = pd.read_csv(cea_path)
    effectiveness = pd.read_csv(effectiveness_path)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5), constrained_layout=True)
    fig.suptitle(f"{sp.city} — CBA Dashboard", fontsize=14, fontweight="bold")

    sizes = 120 + 600 * (cea["PV_Cost_EUR"] / cea["PV_Cost_EUR"].max())
    axes[0].scatter(
        cea["Avoided_Deaths_25y"],
        cea["Cost_per_Death_EUR"] / 1e6,
        s=sizes,
        c=[POLICY_COLORS.get(_short_policy_label(label), "#666666") for label in cea["Policy"]],
        alpha=0.8,
    )
    for _, row in cea.iterrows():
        axes[0].annotate(
            _short_policy_label(str(row["Policy"])),
            (row["Avoided_Deaths_25y"], row["Cost_per_Death_EUR"] / 1e6),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
        )
    axes[0].set_title("Cost-effectiveness frontier")
    axes[0].set_xlabel("Avoided deaths (25y)")
    axes[0].set_ylabel("EUR per avoided death (million)")
    axes[0].grid(alpha=0.2)

    labels, stacks, component_labels = _parse_cost_stack(cba)
    bottoms = np.zeros(len(labels))
    stack_colors = ["#94a3b8", "#64748b", "#0f172a"]
    for idx, component in enumerate(component_labels):
        values = np.array([stack[idx] for stack in stacks], dtype=float) / 1e6
        axes[1].bar(labels, values, bottom=bottoms, color=stack_colors[idx], label=component)
        bottoms += values
    axes[1].set_title("PV cost stack by policy")
    axes[1].set_ylabel("PV cost (million EUR)")
    axes[1].grid(axis="y", alpha=0.2)
    axes[1].legend(frameon=False, fontsize=8)

    colors = [POLICY_COLORS.get(_short_policy_label(label), "#666666") for label in effectiveness["policy"]]
    axes[2].barh(effectiveness["policy"], effectiveness["avoided_deaths_25y"], color=colors, alpha=0.85)
    axes[2].set_title("Policy effectiveness")
    axes[2].set_xlabel("Avoided deaths (25y)")
    axes[2].grid(axis="x", alpha=0.2)

    return [sp.savefig(fig, SECTION_STEMS["6_cba_dashboard"])]


def plot_section_7_uncertainty(sp: SummaryPaths, cfg: dict[str, Any]) -> list[Path]:
    del cfg
    impact_path = sp.optional(
        "uncertainty impact summary",
        sp.uq_dir / f"unc_impact_summary_{sp.slug}_improved.csv",
        sp.uq_dir / f"unc_samples_{sp.slug}_improved.csv",
    )
    sens_aai_path = sp.optional(
        "AAI sensitivity",
        sp.uq_dir / f"sens_aai_agg_{sp.slug}_improved.csv",
    )
    sens_cba_path = sp.optional(
        "CBA/EWS sensitivity",
        sp.uq_dir / f"sens_cba_ews_{sp.slug}_improved.csv",
    )
    if impact_path is None:
        warnings.warn(f"[NB10] Improved uncertainty outputs not found for {sp.slug}.")
        return []

    impact = pd.read_csv(impact_path)
    sens_aai = pd.read_csv(sens_aai_path) if sens_aai_path is not None else None
    sens_cba = pd.read_csv(sens_cba_path) if sens_cba_path is not None else None
    vuln_sens_path = sp.optional(
        "vulnerability sensitivity",
        sp.uq_dir / f"sens_vulnerability_{sp.slug}_improved.csv",
    )
    vuln_sens = pd.read_csv(vuln_sens_path) if vuln_sens_path is not None else None

    ncols = 5 if vuln_sens is not None else 4
    fig, axes = plt.subplots(1, ncols, figsize=(5.5 * ncols, 5), constrained_layout=True)
    if ncols == 1:
        axes = np.array([axes])
    fig.suptitle(f"{sp.city} — Uncertainty and Sensitivity", fontsize=14, fontweight="bold")

    if "aai_agg" in impact.columns:
        vals = pd.to_numeric(impact["aai_agg"], errors="coerce").dropna()
        axes[0].hist(vals, bins=30, color="#60a5fa", edgecolor="white", alpha=0.85)
        if not vals.empty:
            p5, p50, p95 = np.percentile(vals, [5, 50, 95])
            axes[0].axvline(p50, color="#1d4ed8", linewidth=2, linestyle="--", label=f"Median {p50:.2f}")
            axes[0].axvspan(p5, p95, color="#bfdbfe", alpha=0.4, label=f"P5-P95 {p5:.2f}-{p95:.2f}")
            axes[0].legend(frameon=False, fontsize=8)
        axes[0].set_title("AAI distribution")
        axes[0].set_xlabel("AAI aggregate")
        axes[0].set_ylabel("Count")
        axes[0].grid(alpha=0.2)
    else:
        _placeholder(axes[0], "AAI distribution", "aai_agg unavailable")

    freq_path = sp.optional("frequency curve", sp.uq_dir / f"unc_freq_curve_{sp.slug}_improved.csv")
    if freq_path is not None:
        freq = pd.read_csv(freq_path)
    else:
        freq = impact[[col for col in ("rp2", "rp5", "rp10", "rp20") if col in impact.columns]].copy()
    rp_cols = [col for col in ("rp2", "rp5", "rp10", "rp20") if col in freq.columns]
    if rp_cols:
        rps = [int(col.replace("rp", "")) for col in rp_cols]
        medians = [pd.to_numeric(freq[col], errors="coerce").median() for col in rp_cols]
        p5s = [pd.to_numeric(freq[col], errors="coerce").quantile(0.05) for col in rp_cols]
        p95s = [pd.to_numeric(freq[col], errors="coerce").quantile(0.95) for col in rp_cols]
        axes[1].fill_between(rps, p5s, p95s, color="#c7d2fe", alpha=0.55)
        axes[1].plot(rps, medians, color="#4338ca", marker="o", linewidth=2)
        axes[1].set_title("Frequency curve uncertainty")
        axes[1].set_xlabel("Return period (years)")
        axes[1].set_ylabel("Deaths")
        axes[1].grid(alpha=0.2)
    else:
        _placeholder(axes[1], "Frequency curve uncertainty", "Frequency outputs unavailable")

    if sens_aai is not None and {"si", "param", "aai_agg"}.issubset(sens_aai.columns):
        median_rows = sens_aai[sens_aai["si"] == "median"].dropna(subset=["aai_agg"]).copy()
        top = median_rows.nlargest(10, "aai_agg").sort_values("aai_agg")
        axes[2].barh(top["param"], top["aai_agg"], color="#fb923c", alpha=0.9)
        axes[2].set_title("Top AAI sensitivities")
        axes[2].set_xlabel("PAWN median")
        axes[2].grid(axis="x", alpha=0.2)
    else:
        _placeholder(axes[2], "Top AAI sensitivities", "AAI sensitivity CSV unavailable")

    if sens_cba is not None and {"si", "param"}.issubset(sens_cba.columns):
        median_rows = sens_cba[sens_cba["si"] == "median"].copy()
        target = _select_sensitivity_column(
            median_rows,
            (
                "ews_net_avoided_deaths_25y_cum",
                "ews_cost_per_net_death_25y_cum",
                "ews_pv_cost_25y",
            ),
        )
        if target is not None:
            top = median_rows.dropna(subset=[target]).nlargest(10, target).sort_values(target)
            axes[3].barh(top["param"], top[target], color="#60a5fa", alpha=0.9)
            axes[3].set_title(target.replace("_", " "))
            axes[3].set_xlabel("PAWN median")
            axes[3].grid(axis="x", alpha=0.2)
        else:
            _placeholder(axes[3], "EWS/CBA sensitivities", "No populated sensitivity target")
    else:
        _placeholder(axes[3], "EWS/CBA sensitivities", "CBA/EWS sensitivity CSV unavailable")

    if vuln_sens is not None:
        target = _select_sensitivity_column(
            vuln_sens if "si" not in vuln_sens.columns else vuln_sens[vuln_sens["si"] == "median"],
            (),
        )
        df_plot = vuln_sens if "si" not in vuln_sens.columns else vuln_sens[vuln_sens["si"] == "median"]
        if target is not None and "param" in df_plot.columns:
            top = df_plot.dropna(subset=[target]).nlargest(10, target).sort_values(target)
            axes[4].barh(top["param"], top[target], color="#c084fc", alpha=0.9)
            axes[4].set_title("Vulnerability sensitivities")
            axes[4].set_xlabel("PAWN median")
            axes[4].grid(axis="x", alpha=0.2)
        else:
            _placeholder(axes[4], "Vulnerability sensitivities", "No populated vulnerability target")

    return [sp.savefig(fig, SECTION_STEMS["7_uncertainty"])]


def _build_summary_metrics(sp: SummaryPaths, cfg: dict[str, Any]) -> pd.DataFrame:
    scenario = str(cfg.get("exp_scenario", "SSP2")).upper()
    mask = _load_city_mask(sp)
    rows: list[dict[str, Any]] = []

    def add(section: str, metric: str, value: Any, unit: str = "") -> None:
        rows.append({"section": section, "metric": metric, "value": value, "unit": unit})

    pop = _npz_dict(_resolve_population_npz(sp)).get("pop")
    if pop is not None:
        add("city_profile", "population_2020", round(float(np.nansum(np.where(mask, pop, np.nan)))), "people")

    hazard_csv = pd.read_csv(_resolve_hazard_yearly_csv(sp)).sort_values("year")
    for year in (2020, 2050):
        subset = hazard_csv[hazard_csv["year"] == year]
        if not subset.empty:
            add("hazard", f"citymean_degC_{year}", round(float(subset["citymean_degC"].iloc[0]), 2), "degC")
    if {2020, 2050}.issubset(set(hazard_csv["year"])):
        pivot = hazard_csv.set_index("year")
        delta = float(pivot.loc[2050, "citymean_degC"] - pivot.loc[2020, "citymean_degC"])
        add("hazard", "delta_citymean_degC_2050_2020", round(delta, 2), "degC")

    diag_path = _resolve_diag_csv(sp, scenario)
    if diag_path is not None:
        diag = pd.read_csv(diag_path)
    else:
        diag = _compute_vulnerability_diagnostics(sp, scenario, mask)
    if diag is not None and not diag.empty:
        for year in (2020, 2050):
            subset = diag[diag["year"] == year]
            if not subset.empty:
                add("vulnerability", f"svi_mean_{year}", round(float(subset["svi_mean"].iloc[0]), 3))

    deaths = pd.read_csv(_resolve_annual_deaths_csv(sp)).sort_values("year")
    totals = _series_overall(deaths)
    for year in (2020, 2050):
        subset = deaths[deaths["year"] == year]
        if not subset.empty:
            idx = subset.index[0]
            add("mortality", f"baseline_deaths_{year}", round(float(totals.loc[idx]), 1), "deaths/yr")

    policy_path = sp.optional("policy effectiveness", sp.tab_dir / f"{sp.slug}_policy_effectiveness.csv")
    if policy_path is not None:
        policy = pd.read_csv(policy_path)
        for _, row in policy.iterrows():
            add(
                "policy",
                f"avoided_deaths_25y_{str(row['policy']).lower().replace(' ', '_').replace('(', '').replace(')', '')}",
                round(float(row["avoided_deaths_25y"]), 1),
                "deaths",
            )

    cea_path = sp.optional("CEA summary", sp.tab_dir / f"{sp.slug}_cea_summary.csv")
    if cea_path is not None:
        cea = pd.read_csv(cea_path)
        if not cea.empty:
            best = cea.loc[cea["Cost_per_Death_EUR"].idxmin()]
            add("cba", "best_cost_effectiveness_policy", best["Policy"])
            add("cba", "best_cost_per_death", round(float(best["Cost_per_Death_EUR"]), 0), "EUR")

    impact_path = sp.optional(
        "uncertainty impact summary",
        sp.uq_dir / f"unc_impact_summary_{sp.slug}_improved.csv",
        sp.uq_dir / f"unc_samples_{sp.slug}_improved.csv",
    )
    if impact_path is not None:
        impact = pd.read_csv(impact_path)
        if "aai_agg" in impact.columns:
            vals = pd.to_numeric(impact["aai_agg"], errors="coerce").dropna()
            if not vals.empty:
                add("uncertainty", "aai_agg_median", round(float(vals.median()), 3))
                add("uncertainty", "aai_agg_p5", round(float(vals.quantile(0.05)), 3))
                add("uncertainty", "aai_agg_p95", round(float(vals.quantile(0.95)), 3))

    return pd.DataFrame(rows)


def run_nb10_summary(
    city: str | None = None,
    *,
    verbose: bool = True,
) -> dict[str, list[Path]]:
    if city is None:
        city = os.environ.get("CITY", "").strip().lower()
        if not city:
            raise ValueError("Pass city='rome' (or set the CITY environment variable).")
    city = city.lower()

    root = find_repo_root(Path(__file__).resolve())
    cfg_path = root / "configs" / f"{city}.yml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)

    slug = str(cfg.get("slug", city)).lower()
    city_name = str(cfg.get("city_name", city.title()))
    sp = _build_paths(slug, city_name, root)
    if not sp.out.exists():
        raise FileNotFoundError(f"Output directory not found: {sp.out}")

    if verbose:
        print(f"NB10 Summary Dashboard — {city_name} ({slug})")
        print(f"Using outputs in {sp.out}")

    results: dict[str, list[Path]] = {}
    sections = [
        ("1_city_profile", plot_section_1_city_profile),
        ("2_climate_hazard", plot_section_2_climate_hazard),
        ("3_vulnerability", plot_section_3_vulnerability),
        ("4_mortality_if", plot_section_4_mortality_if),
        ("5_policy_levers", plot_section_5_policy_levers),
        ("6_cba_dashboard", plot_section_6_cba_dashboard),
        ("7_uncertainty", plot_section_7_uncertainty),
    ]

    for name, func in sections:
        try:
            paths = func(sp, cfg)
            results[name] = paths
            if verbose and paths:
                for path in paths:
                    print(f"[{name}] saved -> {path}")
            elif verbose:
                print(f"[{name}] skipped")
        except FileNotFoundError as exc:
            results[name] = []
            if verbose:
                print(f"[{name}] missing -> {exc}")
        except Exception as exc:
            results[name] = []
            if verbose:
                print(f"[{name}] error -> {exc}")

    try:
        metrics = _build_summary_metrics(sp, cfg)
        if not metrics.empty:
            sp.tab_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = sp.tab_dir / f"{slug}_framework_summary_metrics.csv"
            metrics.to_csv(metrics_path, index=False)
            results["summary_metrics"] = [metrics_path]
            if verbose:
                print(f"[summary_metrics] saved -> {metrics_path}")
    except Exception as exc:
        if verbose:
            print(f"[summary_metrics] error -> {exc}")

    if verbose:
        saved = sum(len(paths) for paths in results.values())
        print(f"Done. {saved} outputs saved.")
    return results
