"""Variant-only NB10 summary wrapper for March2026 Masselot-main notebooks.

This wrapper extends ``cityheat.nb10_summary`` for the Masselot-main variant:

1. Reroutes paths to ``outputs_variants/masselot_main/{city}``.
2. Adds run-config metric rows surfacing NB04 audit + NB09 LHS scope metadata.
3. Overrides Section 8 (IF-family sensitivity) so it mixes the Masselot headline
   LHS samples (which only contain ``masselot`` and ``masselot_tail``) with the
   Burke-conditional LHS bands (``tables/uncertainty_burke_sensitivity/{family}/``)
   to produce a coherent 4-family AAI comparison.
4. Adds a new Section 9 explicitly comparing the Masselot headline against both
   Burke-conditional bands (AAI CDFs, frequency-curve envelopes, P5/P50/P95
   summaries, and PAWN top-axis comparison).
5. Extends ``_build_summary_metrics`` with per-family P5/P50/P95 from bands and
   corrected Masselot-vs-Burke median ratios.

The base ``cityheat.nb10_summary`` module is untouched so the Burke-main
workflow remains intact. Only the wrapper is rerouted by the per-city
masselot-main notebook (``10_summary_0126_*.ipynb``).
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from . import nb10_summary as _base
from .nbsetup import find_repo_root
from .nbsetup_masselot_main import (
    get_if_main_family,
    get_output_variant,
    if_main_source,
    masselot_extrapolation,
    resolve_city_output,
)


_BURKE_SENSITIVITY_SUBDIR = "uncertainty_burke_sensitivity"
_BURKE_FAMILIES = ("burke_polynomial", "burke_powerlaw")
_MASSELOT_FAMILIES = ("masselot", "masselot_tail")
_ALL_FAMILIES = _MASSELOT_FAMILIES + _BURKE_FAMILIES


_SECTION_9_STEM = "summary_09_if_source_comparison"


_ORIGINAL_BUILD_SUMMARY_METRICS = None


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file if it exists; return None on missing/parse errors.

    Used to surface NB04/NB09 run metadata in the NB10 summary without making
    NB10 fail when those upstream artifacts have not yet been produced (older
    runs that predate the manifest/metadata schema).
    """
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _build_paths(slug: str, city: str, root: Path) -> _base.SummaryPaths:
    out = resolve_city_output(root, slug, get_output_variant())
    return _base.SummaryPaths(
        slug=slug,
        city=city,
        root=root,
        out=out,
        int_dir=out / "interim",
        tab_dir=out / "tables",
        fig_dir=out / "figures",
        summary_dir=out / "figures" / "summary",
        uq_dir=out / "tables" / "uncertainty_improved_fast",
    )


# Burke-conditional band loaders

def _burke_band_uq_dir(sp: _base.SummaryPaths, family: str) -> Path:
    """Return the per-family Burke-conditional LHS output directory."""
    return sp.tab_dir / _BURKE_SENSITIVITY_SUBDIR / family


def _load_burke_band_impact_summary(
    sp: _base.SummaryPaths, family: str
) -> pd.DataFrame | None:
    path = (
        _burke_band_uq_dir(sp, family)
        / f"unc_impact_summary_{sp.slug}_improved_fast.csv"
    )
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError):
        return None


def _load_burke_band_sens_aai(
    sp: _base.SummaryPaths, family: str
) -> pd.DataFrame | None:
    path = _burke_band_uq_dir(sp, family) / f"sens_aai_agg_{sp.slug}_improved_fast.csv"
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError):
        return None


def _load_burke_band_freq_curve(
    sp: _base.SummaryPaths, family: str
) -> pd.DataFrame | None:
    path = (
        _burke_band_uq_dir(sp, family) / f"unc_freq_curve_{sp.slug}_improved_fast.csv"
    )
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError):
        return None


def _aai_distribution_stats(values: pd.Series) -> dict[str, float] | None:
    """Return {p05,p25,p50,p75,p95,mean,n} for a Series of AAI values, or None."""
    vals = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if vals.empty:
        return None
    return {
        "p05": float(vals.quantile(0.05)),
        "p25": float(vals.quantile(0.25)),
        "p50": float(vals.quantile(0.50)),
        "p75": float(vals.quantile(0.75)),
        "p95": float(vals.quantile(0.95)),
        "mean": float(vals.mean()),
        "n": int(vals.size),
    }


def _load_four_family_aai_stats(sp: _base.SummaryPaths) -> pd.DataFrame:
    """Return AAI distribution stats for all four IF families.

    Masselot families come from the headline LHS samples (grouped by
    ``if_family``); Burke families come from the conditional band
    ``unc_impact_summary`` CSVs. Returns a DataFrame with columns
    ``family, source, p05, p25, p50, p75, p95, mean, n``. Families with no
    available data are omitted.
    """
    records: list[dict[str, Any]] = []

    # Masselot families: read headline samples once, group by if_family.
    samples_path = sp.uq_dir / f"unc_samples_{sp.slug}_improved_fast.csv"
    if samples_path.exists():
        try:
            samples = pd.read_csv(samples_path)
        except (OSError, pd.errors.ParserError):
            samples = None
        if samples is not None and {"if_family", "aai_agg"}.issubset(samples.columns):
            samples = samples.copy()
            samples["if_family"] = samples["if_family"].astype(str)
            for family in _MASSELOT_FAMILIES:
                stats = _aai_distribution_stats(
                    samples.loc[samples["if_family"] == family, "aai_agg"]
                )
                if stats is None:
                    continue
                records.append({"family": family, "source": "masselot_headline_lhs", **stats})

    # Burke families: read each band's impact summary independently.
    for family in _BURKE_FAMILIES:
        df = _load_burke_band_impact_summary(sp, family)
        if df is None or "aai_agg" not in df.columns:
            continue
        stats = _aai_distribution_stats(df["aai_agg"])
        if stats is None:
            continue
        records.append({"family": family, "source": "burke_conditional_band", **stats})

    if not records:
        return pd.DataFrame(
            columns=[
                "family",
                "source",
                "p05",
                "p25",
                "p50",
                "p75",
                "p95",
                "mean",
                "n",
            ]
        )

    df = pd.DataFrame(records)
    df["family"] = pd.Categorical(df["family"], categories=list(_ALL_FAMILIES), ordered=True)
    df = df.sort_values("family").reset_index(drop=True)
    df["family"] = df["family"].astype(str)
    return df


def _load_four_family_freq_curves(sp: _base.SummaryPaths) -> dict[str, pd.DataFrame]:
    """Return per-family freq-curve DataFrames (rp2/rp5/rp10/rp20 LHS samples).

    Masselot families share the same headline freq-curve CSV (the headline LHS
    samples both tail modes uniformly), so both keys map to the same DataFrame.
    Burke families read from their own conditional band freq-curve CSV.
    """
    curves: dict[str, pd.DataFrame] = {}

    headline_path = sp.uq_dir / f"unc_freq_curve_{sp.slug}_improved_fast.csv"
    headline_curve: pd.DataFrame | None = None
    if headline_path.exists():
        try:
            headline_curve = pd.read_csv(headline_path)
        except (OSError, pd.errors.ParserError):
            headline_curve = None
    if headline_curve is not None:
        # Headline freq-curve is over the combined Masselot LHS (both tail
        # modes). Without re-grouping by if_family inside the freq-curve CSV
        # (which does not carry that column), we surface a single ``masselot``
        # entry for the combined Masselot uncertainty envelope.
        curves["masselot_headline"] = headline_curve

    for family in _BURKE_FAMILIES:
        df = _load_burke_band_freq_curve(sp, family)
        if df is not None:
            curves[family] = df

    return curves


def _top_pawn_axes_per_source(
    sp: _base.SummaryPaths, n_top: int = 5
) -> dict[str, pd.DataFrame]:
    """Return top-N median-PAWN axes for headline + each Burke conditional band.

    Drops the IF_FAMILY_IDX axis because its variance differs by definition
    across bands (headline: 2 levels; conditional band: 1 level) and the
    comparison would be misleading.
    """
    drop_params = {"IF_FAMILY_IDX"}
    out: dict[str, pd.DataFrame] = {}

    headline_path = sp.uq_dir / f"sens_aai_agg_{sp.slug}_improved_fast.csv"
    if headline_path.exists():
        try:
            df = pd.read_csv(headline_path)
        except (OSError, pd.errors.ParserError):
            df = None
        if df is not None and {"si", "param", "aai_agg"}.issubset(df.columns):
            sub = df.loc[df["si"].astype(str).str.lower() == "median"].copy()
            sub["aai_agg"] = pd.to_numeric(sub["aai_agg"], errors="coerce")
            sub = sub.dropna(subset=["param", "aai_agg"])
            sub = sub.loc[~sub["param"].astype(str).isin(drop_params)]
            out["masselot_headline"] = sub.nlargest(n_top, "aai_agg").reset_index(drop=True)

    for family in _BURKE_FAMILIES:
        df = _load_burke_band_sens_aai(sp, family)
        if df is None or not {"si", "param", "aai_agg"}.issubset(df.columns):
            continue
        sub = df.loc[df["si"].astype(str).str.lower() == "median"].copy()
        sub["aai_agg"] = pd.to_numeric(sub["aai_agg"], errors="coerce")
        sub = sub.dropna(subset=["param", "aai_agg"])
        sub = sub.loc[~sub["param"].astype(str).isin(drop_params)]
        out[family] = sub.nlargest(n_top, "aai_agg").reset_index(drop=True)

    return out

# Section 8 override (4-family AAI sensitivity from mixed sources)

def _short_family_label(family: str) -> str:
    return _base.IF_FAMILY_SHORT_LABELS.get(family, family)


def _long_family_label(family: str) -> str:
    return _base.IF_FAMILY_LABELS.get(family, family)


def _family_color(family: str) -> str:
    return _base.IF_FAMILY_COLORS.get(family, "#64748b")


def plot_section_8_if_family_sensitivity(
    sp: _base.SummaryPaths, cfg: dict[str, Any]
) -> list[Path]:
    """Replacement for the base Section 8 that mixes headline + Burke bands.

    The base implementation expects ``samples["if_family"]`` to contain all
    four IF families. Under masselot-main the headline LHS scope is restricted
    to ``{masselot, masselot_tail}``, so the base figure silently drops Burke.
    This override reconstructs the 4-family comparison by reading Masselot
    stats from the headline samples and Burke stats from the conditional band
    ``unc_impact_summary`` CSVs.
    """
    del cfg

    stats = _load_four_family_aai_stats(sp)
    if stats.empty:
        warnings.warn(
            f"[NB10 masselot-main] Section 8: no IF-family AAI stats available "
            f"for {sp.slug}; skipping."
        )
        return []

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), constrained_layout=True)
    fig.suptitle(
        f"{sp.city} — Impact-Function Family Sensitivity (Masselot-main)",
        fontsize=14,
        fontweight="bold",
    )

    # Left panel: AAI distribution per family (P5-P95 whiskers, P25-P75 box,
    # P50 marker). Symlog if the cross-family range is very wide.
    y = np.arange(len(stats))
    for ypos, row in zip(y, stats.to_dict("records")):
        color = _family_color(row["family"])
        axes[0].hlines(ypos, row["p05"], row["p95"], color=color, linewidth=3, alpha=0.30)
        axes[0].hlines(ypos, row["p25"], row["p75"], color=color, linewidth=8, alpha=0.70)
        axes[0].scatter(
            row["p50"],
            ypos,
            s=80,
            color=color,
            edgecolor="white",
            linewidth=0.9,
            zorder=3,
        )
    axes[0].set_yticks(y, [_long_family_label(f) for f in stats["family"]])
    axes[0].invert_yaxis()
    axes[0].set_title("AAI distribution by IF family\n(P50 marker, IQR thick, P5-P95 thin)")
    axes[0].set_xlabel("AAI aggregate (deaths/yr)")
    axes[0].grid(axis="x", alpha=0.25)
    max_p95 = float(stats["p95"].max())
    positive_medians = stats.loc[stats["p50"] > 0, "p50"]
    scale_ref = float(positive_medians.median()) if not positive_medians.empty else max_p95
    if np.isfinite(max_p95) and np.isfinite(scale_ref) and scale_ref > 0 and max_p95 / scale_ref > 50:
        linthresh = max(scale_ref / 5, max_p95 / 1000, 1e-6)
        axes[0].set_xscale("symlog", linthresh=linthresh)
        axes[0].set_xlim(left=0)
        axes[0].set_xlabel("AAI aggregate (deaths/yr, symlog)")

    # Right panel: top 5 median PAWN axes for the Masselot headline LHS.
    # The Burke-conditional bands' PAWN rankings are surfaced in Section 9.
    headline_pawn = _top_pawn_axes_per_source(sp, n_top=10).get("masselot_headline")
    if headline_pawn is not None and not headline_pawn.empty:
        top = headline_pawn.sort_values("aai_agg")
        labels = [_base.PARAM_LABELS.get(p, str(p)) for p in top["param"]]
        axes[1].barh(labels, top["aai_agg"], color="#fb923c", alpha=0.9)
        axes[1].set_title("Top 10 PAWN axes — Masselot headline LHS")
        axes[1].set_xlabel("PAWN median KS")
        axes[1].grid(axis="x", alpha=0.25)
    else:
        _base._placeholder(
            axes[1],
            "Top PAWN axes — Masselot headline LHS",
            "sens_aai_agg unavailable",
        )

    return [sp.savefig(fig, _base.SECTION_STEMS["8_if_family_sensitivity"])]


# Section 9: explicit Masselot-vs-Burke comparison

def plot_section_9_if_source_comparison(
    sp: _base.SummaryPaths, cfg: dict[str, Any]
) -> list[Path]:
    """Explicit IF-source comparison: Masselot headline vs Burke conditional bands.

    2x2 layout:
        (a) AAI cumulative distribution overlay for all 4 IF families.
        (b) Frequency curve uncertainty envelopes (P5-P95) for all 4 families.
        (c) Per-family AAI summary as P5-P50-P95 horizontal bars.
        (d) Top-5 PAWN axis comparison across the three uncertainty sources
            (Masselot headline, Burke polynomial band, Burke power-law band).

    Saves to ``figures/summary/summary_09_if_source_comparison_{slug}.png``.
    """
    del cfg

    stats = _load_four_family_aai_stats(sp)
    if stats.empty:
        warnings.warn(
            f"[NB10 masselot-main] Section 9: no IF-family AAI stats available "
            f"for {sp.slug}; skipping."
        )
        return []

    fig, axes = plt.subplots(2, 2, figsize=(15, 11), constrained_layout=True)
    fig.suptitle(
        f"{sp.city} — IF Source Comparison "
        "(Masselot headline LHS vs Burke conditional bands)",
        fontsize=14,
        fontweight="bold",
    )

    ax_cdf, ax_freq = axes[0, 0], axes[0, 1]
    ax_bars, ax_pawn = axes[1, 0], axes[1, 1]

    # (a) AAI CDF overlay: each family's distribution shown as a step CDF on
    # the same axes. Masselot families from headline samples filtered by
    # if_family; Burke families from band unc_impact_summary.
    cdf_drawn = False
    samples_path = sp.uq_dir / f"unc_samples_{sp.slug}_improved_fast.csv"
    samples = None
    if samples_path.exists():
        try:
            samples = pd.read_csv(samples_path)
        except (OSError, pd.errors.ParserError):
            samples = None

    aai_max_global = 0.0
    for family in _ALL_FAMILIES:
        if family in _MASSELOT_FAMILIES:
            if samples is None or "if_family" not in samples.columns or "aai_agg" not in samples.columns:
                continue
            vals = pd.to_numeric(
                samples.loc[samples["if_family"].astype(str) == family, "aai_agg"],
                errors="coerce",
            ).replace([np.inf, -np.inf], np.nan).dropna()
        else:
            band = _load_burke_band_impact_summary(sp, family)
            if band is None or "aai_agg" not in band.columns:
                continue
            vals = pd.to_numeric(band["aai_agg"], errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            ).dropna()
        if vals.empty:
            continue
        sorted_vals = np.sort(vals.to_numpy())
        cdf = np.arange(1, len(sorted_vals) + 1) / len(sorted_vals)
        ax_cdf.step(
            sorted_vals,
            cdf,
            where="post",
            color=_family_color(family),
            linewidth=2.2,
            label=_long_family_label(family),
        )
        aai_max_global = max(aai_max_global, float(sorted_vals[-1]))
        cdf_drawn = True

    if cdf_drawn:
        ax_cdf.set_title("AAI cumulative distribution by IF family")
        ax_cdf.set_xlabel("AAI aggregate (deaths/yr)")
        ax_cdf.set_ylabel("Cumulative probability")
        ax_cdf.grid(alpha=0.25)
        ax_cdf.legend(frameon=False, fontsize=8)
        ax_cdf.set_ylim(0, 1.02)
        # Symlog x-axis if dynamic range is wide (Burke for cold cities).
        positive_medians = stats.loc[stats["p50"] > 0, "p50"]
        scale_ref = (
            float(positive_medians.median()) if not positive_medians.empty else aai_max_global
        )
        if scale_ref > 0 and aai_max_global / scale_ref > 50:
            linthresh = max(scale_ref / 5, aai_max_global / 1000, 1e-6)
            ax_cdf.set_xscale("symlog", linthresh=linthresh)
            ax_cdf.set_xlim(left=0)
            ax_cdf.set_xlabel("AAI aggregate (deaths/yr, symlog)")
    else:
        _base._placeholder(ax_cdf, "AAI CDF by IF family", "No finite AAI samples")

    # (b) Frequency curve uncertainty envelopes: median + P5-P95 shaded band
    # for each available source (Masselot combined headline + each Burke band).
    curves = _load_four_family_freq_curves(sp)
    rp_cols = ("rp2", "rp5", "rp10", "rp20")
    rps = [2, 5, 10, 20]
    freq_drawn = False
    legend_pairs: list[tuple[str, str]] = [
        ("masselot_headline", "Masselot headline LHS (both tails)"),
        ("burke_polynomial", _long_family_label("burke_polynomial") + " (conditional band)"),
        ("burke_powerlaw", _long_family_label("burke_powerlaw") + " (conditional band)"),
    ]
    for key, label in legend_pairs:
        df = curves.get(key)
        if df is None:
            continue
        cols_present = [c for c in rp_cols if c in df.columns]
        if not cols_present:
            continue
        x = [rps[rp_cols.index(c)] for c in cols_present]
        medians = [pd.to_numeric(df[c], errors="coerce").median() for c in cols_present]
        p5 = [pd.to_numeric(df[c], errors="coerce").quantile(0.05) for c in cols_present]
        p95 = [pd.to_numeric(df[c], errors="coerce").quantile(0.95) for c in cols_present]
        color = (
            _family_color("masselot_tail")
            if key == "masselot_headline"
            else _family_color(key)
        )
        ax_freq.fill_between(x, p5, p95, color=color, alpha=0.20)
        ax_freq.plot(x, medians, color=color, marker="o", linewidth=2.0, label=label)
        freq_drawn = True

    if freq_drawn:
        ax_freq.set_title("Frequency curve uncertainty by IF source")
        ax_freq.set_xlabel("Return period (years)")
        ax_freq.set_ylabel("Deaths per event")
        ax_freq.grid(alpha=0.25)
        ax_freq.legend(frameon=False, fontsize=8, loc="best")
    else:
        _base._placeholder(
            ax_freq, "Frequency curve uncertainty", "Frequency-curve CSVs unavailable"
        )

    # (c) Per-family AAI summary as P5-P50-P95 horizontal bars (same content as
    # Section 8 left panel but with explicit numeric annotations and the
    # Masselot-vs-Burke ratios annotated as text on the panel).
    y = np.arange(len(stats))
    for ypos, row in zip(y, stats.to_dict("records")):
        color = _family_color(row["family"])
        ax_bars.hlines(ypos, row["p05"], row["p95"], color=color, linewidth=3, alpha=0.30)
        ax_bars.hlines(ypos, row["p25"], row["p75"], color=color, linewidth=8, alpha=0.70)
        ax_bars.scatter(
            row["p50"], ypos, s=80, color=color, edgecolor="white", linewidth=0.9, zorder=3
        )
        ax_bars.annotate(
            f"P50={row['p50']:.3g}",
            xy=(row["p50"], ypos),
            xytext=(6, 8),
            textcoords="offset points",
            fontsize=8,
            color="#1f2937",
        )
    ax_bars.set_yticks(y, [_long_family_label(f) for f in stats["family"]])
    ax_bars.invert_yaxis()
    ax_bars.set_title("AAI summary statistics (P5-P25-P50-P75-P95)")
    ax_bars.set_xlabel("AAI aggregate (deaths/yr)")
    ax_bars.grid(axis="x", alpha=0.25)
    max_p95 = float(stats["p95"].max())
    positive_medians = stats.loc[stats["p50"] > 0, "p50"]
    scale_ref = float(positive_medians.median()) if not positive_medians.empty else max_p95
    if np.isfinite(max_p95) and np.isfinite(scale_ref) and scale_ref > 0 and max_p95 / scale_ref > 50:
        linthresh = max(scale_ref / 5, max_p95 / 1000, 1e-6)
        ax_bars.set_xscale("symlog", linthresh=linthresh)
        ax_bars.set_xlim(left=0)
        ax_bars.set_xlabel("AAI aggregate (deaths/yr, symlog)")

    # Annotate Masselot-vs-Burke ratios as a small textbox in the corner.
    medians_by_family = {row["family"]: row["p50"] for row in stats.to_dict("records")}

    def _ratio(num_family: str, den_family: str) -> str:
        n = medians_by_family.get(num_family, np.nan)
        d = medians_by_family.get(den_family, np.nan)
        if not np.isfinite(n) or not np.isfinite(d) or d <= 0:
            return "n/a"
        return f"{(n / d):.2f}x"

    ratio_lines = [
        "Masselot vs Burke P50 ratios:",
        f"  M-tail / B-poly  = {_ratio('masselot_tail', 'burke_polynomial')}",
        f"  M-tail / B-power = {_ratio('masselot_tail', 'burke_powerlaw')}",
        f"  M-tail / M-const = {_ratio('masselot_tail', 'masselot')}",
    ]
    ax_bars.text(
        0.98,
        0.02,
        "\n".join(ratio_lines),
        transform=ax_bars.transAxes,
        ha="right",
        va="bottom",
        fontsize=8.5,
        bbox=dict(boxstyle="round,pad=0.45", fc="#fefce8", ec="#ca8a04", alpha=0.95),
    )

    # (d) Top-5 PAWN axes per source, plotted as three small horizontal bar
    # groups stacked in the same axes (different colors per source).
    pawn_per_source = _top_pawn_axes_per_source(sp, n_top=5)
    pawn_legend = [
        ("masselot_headline", _family_color("masselot_tail"), "Masselot headline LHS"),
        ("burke_polynomial", _family_color("burke_polynomial"), "Burke polynomial band"),
        ("burke_powerlaw", _family_color("burke_powerlaw"), "Burke power-law band"),
    ]
    available = [(k, c, label) for (k, c, label) in pawn_legend if pawn_per_source.get(k) is not None and not pawn_per_source[k].empty]
    if available:
        # Build a stacked layout: each source contributes one labeled "block"
        # of 5 horizontal bars. Total: up to 15 bars. We use a single shared
        # axis for visual comparability of KS magnitudes.
        all_records: list[tuple[str, str, str, float]] = []
        for source_key, color, label in available:
            df = pawn_per_source[source_key].sort_values("aai_agg")
            for _, row in df.iterrows():
                all_records.append((source_key, color, _base.PARAM_LABELS.get(str(row["param"]), str(row["param"])), float(row["aai_agg"])))
        if all_records:
            y_positions = np.arange(len(all_records))
            for idx, (_, color, _label, value) in enumerate(all_records):
                ax_pawn.barh(y_positions[idx], value, color=color, alpha=0.85)
            # Use the param name as ytick, but add a source-suffix to avoid
            # duplicate labels confusing the eye.
            yticklabels = []
            for (source_key, _color, label, _value) in all_records:
                src_short = {
                    "masselot_headline": "M-head",
                    "burke_polynomial": "B-poly",
                    "burke_powerlaw": "B-power",
                }[source_key]
                yticklabels.append(f"{label} [{src_short}]")
            ax_pawn.set_yticks(y_positions, yticklabels, fontsize=8)
            ax_pawn.invert_yaxis()
            ax_pawn.set_title("Top-5 PAWN axes per uncertainty source")
            ax_pawn.set_xlabel("PAWN median KS")
            ax_pawn.grid(axis="x", alpha=0.25)
            # Add a custom legend using proxy patches to identify the three
            # sources by colour (since they all share a single axis).
            from matplotlib.patches import Patch

            legend_handles = [
                Patch(facecolor=color, alpha=0.85, label=label)
                for (_, color, label) in available
            ]
            ax_pawn.legend(handles=legend_handles, frameon=False, fontsize=8, loc="lower right")
        else:
            _base._placeholder(ax_pawn, "Top PAWN axes per source", "No median PAWN rows")
    else:
        _base._placeholder(
            ax_pawn, "Top PAWN axes per source", "Burke band sens CSVs unavailable"
        )

    return [sp.savefig(fig, _SECTION_9_STEM)]


# Extended summary metrics

def _build_summary_metrics(sp: _base.SummaryPaths, cfg: dict[str, Any]) -> pd.DataFrame:
    base_builder = _ORIGINAL_BUILD_SUMMARY_METRICS or _base._build_summary_metrics
    metrics = base_builder(sp, cfg)
    if_main_family = get_if_main_family()
    rows: list[dict[str, Any]] = [
        {
            "section": "run_config",
            "metric": "output_variant",
            "value": get_output_variant(),
            "unit": "",
        },
        {
            "section": "run_config",
            "metric": "if_main_family",
            "value": if_main_family,
            "unit": "",
        },
        {
            "section": "run_config",
            "metric": "if_main_source",
            "value": if_main_source(if_main_family),
            "unit": "",
        },
        {
            "section": "run_config",
            "metric": "masselot_extrapolation",
            "value": masselot_extrapolation(if_main_family),
            "unit": "",
        },
        {
            "section": "run_config",
            "metric": "burke_role",
            "value": "sensitivity" if if_main_family.startswith("masselot") else "main",
            "unit": "",
        },
        {
            "section": "run_config",
            "metric": "current_original_workflow_preserved",
            "value": True,
            "unit": "",
        },
    ]
    if if_main_family == "masselot_tail":
        rows.append(
            {
                "section": "run_config",
                "metric": "masselot_tail_extrapolation",
                "value": "loglinear_tail",
                "unit": "",
            }
        )

    # NB04 Burke recompute audit status
    nb04_manifest = _safe_load_json(sp.int_dir / f"if_family_manifest_{sp.slug}.json")
    if nb04_manifest is not None:
        audit_status = nb04_manifest.get("burke_recompute_audit_status")
        if audit_status is not None:
            rows.append(
                {
                    "section": "run_config",
                    "metric": "burke_recompute_audit_status",
                    "value": str(audit_status),
                    "unit": "",
                }
            )
        fail_count = nb04_manifest.get("burke_recompute_audit_fail_count")
        if fail_count is not None:
            rows.append(
                {
                    "section": "run_config",
                    "metric": "burke_recompute_audit_fail_count",
                    "value": int(fail_count),
                    "unit": "rows",
                }
            )
        audit_strict = nb04_manifest.get("burke_recompute_audit_strict")
        if audit_strict is not None:
            rows.append(
                {
                    "section": "run_config",
                    "metric": "burke_recompute_audit_strict",
                    "value": bool(audit_strict),
                    "unit": "",
                }
            )

    # NB09 headline LHS scope (records what the headline run represents)
    nb09_meta = _safe_load_json(sp.uq_dir / f"uq_dimensions_{sp.slug}_improved_fast.json")
    if nb09_meta is not None:
        for key in ("lhs_scope", "lhs_run_type"):
            value = nb09_meta.get(key)
            if value is not None:
                rows.append(
                    {
                        "section": "run_config",
                        "metric": key,
                        "value": str(value),
                        "unit": "",
                    }
                )
        lhs_sampled = nb09_meta.get("lhs_sampled_families")
        if isinstance(lhs_sampled, list):
            rows.append(
                {
                    "section": "run_config",
                    "metric": "lhs_sampled_families",
                    "value": ",".join(str(f) for f in lhs_sampled),
                    "unit": "",
                }
            )

    # Per-family P5/P50/P95 from headline + Burke bands.
    stats = _load_four_family_aai_stats(sp)
    medians_by_family: dict[str, float] = {}
    for record in stats.to_dict("records"):
        family = str(record["family"])
        for pct in ("p05", "p50", "p95"):
            rows.append(
                {
                    "section": "if_family_sensitivity",
                    "metric": f"aai_agg_{pct}_{family}",
                    "value": round(float(record[pct]), 4),
                    "unit": "deaths/yr",
                }
            )
        rows.append(
            {
                "section": "if_family_sensitivity",
                "metric": f"aai_agg_n_samples_{family}",
                "value": int(record["n"]),
                "unit": "samples",
            }
        )
        rows.append(
            {
                "section": "if_family_sensitivity",
                "metric": f"aai_agg_source_{family}",
                "value": str(record["source"]),
                "unit": "",
            }
        )
        medians_by_family[family] = float(record["p50"])

    def _push_ratio(num_family: str, den_family: str) -> None:
        n = medians_by_family.get(num_family, np.nan)
        d = medians_by_family.get(den_family, np.nan)
        if not (np.isfinite(n) and np.isfinite(d) and d > 0):
            return
        rows.append(
            {
                "section": "if_family_sensitivity",
                "metric": f"aai_agg_ratio_{num_family}_to_{den_family}",
                "value": round(float(n / d), 3),
                "unit": "ratio",
            }
        )

    # Headline canonical (masselot_tail) vs Burke families and vs constant tail.
    _push_ratio("masselot_tail", "burke_polynomial")
    _push_ratio("masselot_tail", "burke_powerlaw")
    _push_ratio("masselot_tail", "masselot")
    _push_ratio("masselot", "burke_polynomial")
    _push_ratio("masselot", "burke_powerlaw")

    run_config = pd.DataFrame(rows)
    return pd.concat([run_config, metrics], ignore_index=True)

# Orchestration: extended run_nb10_summary

def run_nb10_summary(
    city: str | None = None,
    *,
    verbose: bool = True,
) -> dict[str, list[Path]]:
    """Run NB10 with Masselot-main paths, Section 8 override, and new Section 9.

    Compared to a direct call into the base :func:`cityheat.nb10_summary.run_nb10_summary`,
    this wrapper:

    * Routes all output paths to ``outputs_variants/masselot_main/{city}``.
    * Substitutes a Section 8 implementation that handles the masselot-headline
      LHS scope (where the headline samples only contain the two Masselot tail
      modes) by mixing in Burke-conditional band stats.
    * Adds a Section 9 figure (``summary_09_if_source_comparison``) explicitly
      comparing the Masselot headline against both Burke-conditional bands.
    * Augments the framework summary metrics with per-family P5/P50/P95 and
      Masselot-vs-Burke ratios computed from the conditional band medians.

    The base ``cityheat.nb10_summary`` module is monkey-patched only for the
    duration of this call; original references are restored in the ``finally``
    block so the Burke-main workflow remains untouched.
    """
    global _ORIGINAL_BUILD_SUMMARY_METRICS

    if city is None:
        city = os.environ.get("CITY", "").strip().lower()
        if not city:
            raise ValueError(
                "Pass city='rome' (or set the CITY environment variable) before "
                "calling run_nb10_summary."
            )
    city = city.lower()

    original_build_paths = _base._build_paths
    original_build_summary_metrics = _base._build_summary_metrics
    original_section_8 = _base.plot_section_8_if_family_sensitivity

    try:
        _ORIGINAL_BUILD_SUMMARY_METRICS = original_build_summary_metrics
        _base._build_paths = _build_paths
        _base._build_summary_metrics = _build_summary_metrics
        _base.plot_section_8_if_family_sensitivity = plot_section_8_if_family_sensitivity

        if verbose:
            print(f"NB10 Masselot-main wrapper")
            print(f"  Output variant: {get_output_variant()}")
            print(f"  IF main family: {get_if_main_family()}")

        # Call the base orchestrator. It will use our patched paths, our
        # patched Section 8, and our patched summary-metrics builder.
        results = _base.run_nb10_summary(city=city, verbose=verbose)

        # Build a SummaryPaths instance ourselves to run Section 9. This
        # reproduces the path-build logic used inside ``_base.run_nb10_summary``
        # so the two views are identical.
        root = find_repo_root(Path(__file__).resolve())
        cfg_path = root / "configs" / f"{city}.yml"
        if not cfg_path.exists():
            raise FileNotFoundError(f"Config not found: {cfg_path}")
        with open(cfg_path, "r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle)
        slug = str(cfg.get("slug", city)).lower()
        city_name = str(cfg.get("city_name", city.title()))
        sp = _build_paths(slug, city_name, root)

        try:
            section_9_paths = plot_section_9_if_source_comparison(sp, cfg)
            results["9_if_source_comparison"] = section_9_paths
            if verbose:
                if section_9_paths:
                    for path in section_9_paths:
                        print(f"[9_if_source_comparison] saved -> {path}")
                else:
                    print("[9_if_source_comparison] skipped (no Burke bands or no headline samples)")
        except FileNotFoundError as exc:
            results["9_if_source_comparison"] = []
            if verbose:
                print(f"[9_if_source_comparison] missing -> {exc}")
        except Exception as exc:
            results["9_if_source_comparison"] = []
            if verbose:
                print(f"[9_if_source_comparison] error -> {exc}")

        return results
    finally:
        _base._build_paths = original_build_paths
        _base._build_summary_metrics = original_build_summary_metrics
        _base.plot_section_8_if_family_sensitivity = original_section_8
        _ORIGINAL_BUILD_SUMMARY_METRICS = None
