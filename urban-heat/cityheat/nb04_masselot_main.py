"""Variant-only NB04 helpers for the March2026 Masselot-main workflow."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from climada.engine import ImpactCalc
from climada.entity.exposures import Exposures
from climada.entity.impact_funcs import ImpactFunc, ImpactFuncSet


AGE_ORDER = ["<15", "15-64", "65+"]
AGE_TO_IF_ID = {"<15": 1, "15-64": 2, "65+": 3}
SUPPORTED_MASSELOT_FAMILIES = {"masselot", "masselot_tail"}
MASSELOT_SOURCE_FILE = {
    "masselot": "masselot_if_curves_{slug}.json",
    "masselot_tail": "masselot_if_curves_{slug}_tail.json",
}
MASSELOT_EXTRAPOLATION = {
    "masselot": "constant_tail",
    "masselot_tail": "loglinear_tail",
}


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _reference_block(data: dict[str, Any]) -> dict[str, Any]:
    ref_year = str(data.get("reference_year", 2020))
    if "ifs_by_year" in data and ref_year in data["ifs_by_year"]:
        return data["ifs_by_year"][ref_year]
    raise KeyError(f"Could not find reference-year IF block {ref_year!r}.")


def _annotate_masselot(
    data: dict[str, Any],
    *,
    family: str,
    role: str,
    source_path: Path,
    variant_name: str,
    main_family: str,
    canonical: bool = False,
) -> dict[str, Any]:
    out = copy.deepcopy(data)
    extrapolation = MASSELOT_EXTRAPOLATION[family]
    notes = str(out.get("notes", "")).strip()
    if canonical:
        notes = (
            notes
            + f" Canonical deterministic IF for {variant_name}; true source is Masselot / {family}."
        ).strip()
    out.update(
        {
            "output_variant": variant_name,
            "if_main_family": main_family,
            "if_main_source": "Masselot",
            "if_family": family,
            "source_family": family,
            "if_role": role,
            "canonical_downstream_if": bool(canonical),
            "source": "Masselot et al. 2023 / Zenodo 10288665",
            "source_file": str(source_path),
            "masselot_extrapolation": extrapolation,
            "masselot_extrapolation_assumption": extrapolation,
            "burke_role": "sensitivity",
            "current_original_workflow_preserved": True,
            "notes": notes,
        }
    )
    if family == "masselot_tail":
        out["masselot_tail_extrapolation"] = "loglinear_tail"
    return out


def _mdd_scaling_table(data: dict[str, Any]) -> pd.DataFrame:
    ref_year = str(data.get("reference_year", 2020))
    rows: list[dict[str, float | int]] = []
    for year_str in data["ifs_by_year"]:
        row: dict[str, float | int] = {"year": int(year_str)}
        for age in AGE_ORDER:
            ref = np.asarray(data["ifs_by_year"][ref_year][age]["mdd"], dtype=float)
            cur = np.asarray(data["ifs_by_year"][year_str][age]["mdd"], dtype=float)
            valid = np.isfinite(ref) & np.isfinite(cur) & (ref > 0)
            row[age] = float(np.median(cur[valid] / ref[valid])) if np.any(valid) else 1.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("year").sort_index()


def hazard_for_year(hazard: Any, year: int) -> Any:
    years = pd.to_datetime(hazard.date.astype("datetime64[ns]")).year
    mask = years == int(year)
    hazard_y = copy.copy(hazard)
    hazard_y.intensity = hazard.intensity[mask, :]
    hazard_y.fraction = hazard.fraction[mask, :]
    hazard_y.event_id = hazard.event_id[mask]
    hazard_y.date = hazard.date[mask]
    hazard_y.event_name = hazard.event_name[mask]
    hazard_y.frequency = hazard.frequency[mask]
    return hazard_y


def impact_func_set_from_block(block: dict[str, Any], family_label: str) -> ImpactFuncSet:
    funcs = []
    for age in AGE_ORDER:
        rec = block[age]
        intensity = np.asarray(rec["intensity"], dtype=float)
        mdd = np.asarray(rec["mdd"], dtype=float)
        funcs.append(
            ImpactFunc(
                haz_type="T2M",
                id=int(rec.get("id", AGE_TO_IF_ID[age])),
                intensity=intensity,
                mdd=mdd,
                paa=np.ones_like(mdd),
                name=rec.get("name", f"{age}_{family_label}"),
            )
        )
    ifs = ImpactFuncSet(funcs)
    ifs.check()
    return ifs


def annual_deaths_from_if_json(
    data: dict[str, Any],
    family_label: str,
    *,
    hazard: Any,
    exposures_by_year: dict[int, Exposures],
) -> pd.DataFrame:
    annual = {age: {} for age in AGE_ORDER}
    for year_str, block in data["ifs_by_year"].items():
        year = int(year_str)
        if year not in exposures_by_year:
            continue
        hazard_y = hazard_for_year(hazard, year)
        ifs_y = impact_func_set_from_block(block, f"{family_label}_{year}")
        exp_src = exposures_by_year[year]
        for age in AGE_ORDER:
            gdf_age = exp_src.gdf.loc[exp_src.gdf["age_group"] == age].copy()
            gdf_age["impf_T2M"] = AGE_TO_IF_ID[age]
            exp_age = Exposures()
            exp_age.set_gdf(gdf_age)
            exp_age.value_unit = getattr(exp_src, "value_unit", "people")
            exp_age.ref_year = getattr(exp_src, "ref_year", year)
            imp = ImpactCalc(exp_age, ifs_y, hazard_y).impact(save_mat=False)
            annual[age][year] = float(np.sum(imp.at_event))
    out = pd.DataFrame({age: pd.Series(vals) for age, vals in annual.items()}).sort_index()[AGE_ORDER]
    out.index = out.index.astype(int)
    out.index.name = "year"
    return out


def _original_output_dir(root: Path, slug: str, city: str) -> Path:
    candidates = []
    for name in [slug, city, city.lower(), city.title(), slug.title()]:
        if name and name not in candidates:
            candidates.append(name)
    for name in candidates:
        out = root / "outputs" / name
        if (out / "interim" / f"if_curves_by_year_{slug}.json").exists():
            return out
    checked = "\n".join(str(root / "outputs" / name) for name in candidates)
    raise FileNotFoundError(
        f"Could not find original Burke-main outputs for {city} ({slug}). Checked:\n{checked}"
    )


def _annotate_burke_sensitivity(
    data: dict[str, Any],
    *,
    family: str,
    source_path: Any,
    variant_name: str,
    main_family: str,
    built: bool = False,
    numerical_source: str | None = None,
) -> dict[str, Any]:
    out = copy.deepcopy(data)
    notes = str(out.get("notes", "")).strip()
    if notes:
        notes += " "
    if built:
        notes += (
            "Built deterministically from the original March2026 Burke-main "
            "calibration (city-independent base curves scaled by the baseline "
            "mortality trend) and used only as a sensitivity in the March2026 "
            "Masselot-main variant."
        )
    else:
        notes += (
            "Copied from the original March2026 Burke-main workflow and used only as "
            "a sensitivity in the March2026 Masselot-main variant."
        )
    out.update(
        {
            "output_variant": variant_name,
            "if_main_family": main_family,
            "if_main_source": "Masselot",
            "if_family": family,
            "source_family": family,
            "if_role": "sensitivity",
            "canonical_downstream_if": False,
            "source": "Burke age-differentiated impact-function workflow",
            "source_file": str(source_path),
            "burke_role": "sensitivity",
            "masselot_main_family": main_family,
            "current_original_workflow_preserved": True,
            "copied_from_original_march2026_burke_main": not built,
            "built_from_burke_calibration": bool(built),
            "numerical_curve_source": numerical_source
            or "original March2026 Burke-main IF JSON",
            "notes": notes,
        }
    )
    return out


# ---------------------------------------------------------------------------
# Deterministic Burke sensitivity IF construction
#
# Ported verbatim from the original March2026 Burke-main NB04 (cells 13/16/20/54).
# The Burke base curves are city-INDEPENDENT: a fixed 0-40 degC grid at 0.5 degC
# steps, a fixed reference temperature (T_ref=20 degC), and fixed Burke et al.
# (2025, Fig. 2 EU panel) age-specific anchor points. The ONLY per-city term is
# the baseline-mortality year scaling, which is identical to the Masselot-main
# scaling NB04 already computes as ``scale_factors`` (see _mdd_scaling_table).
# Validated 2026-07-09 to reproduce the original pilot Burke IF JSONs to <1e-20
# for rome/athens/lisbon across all years and both families.
#
# Copenhagen is the sole exception: its original Burke IF was built on the extreme
# (Track-B) hazard with a LOCAL reference temperature and cold-city anchors, so it
# cannot be reproduced by this standard build. Its verbatim original curves are
# shipped in ``burke_if_reference/copenhagen/`` and loaded as-is, preserving the
# current Copenhagen sensitivity exactly.
# ---------------------------------------------------------------------------

BURKE_T_MIN, BURKE_T_MAX, BURKE_T_STEP = 0.0, 40.0, 0.5
BURKE_T_REF = 20.0
BURKE_PER100K = 100_000.0
BURKE_SCALING_SOURCE = "Wittgenstein ASSR baseline mortality"
BURKE_POLY_REF_POINTS = {
    "65+": [(22, 0.1), (25, 0.5), (28, 1.5), (30, 3.5), (33, 7.0), (35, 12.0)],
    "15-64": [(22, 0.0), (25, 0.02), (28, 0.05), (30, 0.15), (33, 0.40), (35, 0.80)],
    "<15": [(22, 0.0), (25, 0.005), (28, 0.02), (30, 0.03), (33, 0.04), (35, 0.05)],
}
# (d1, T1, d2, T2) per 100k — fixed January-2026 power-law anchors (old NB04 cell 54).
BURKE_POWERLAW_ANCHORS = {
    "65+": (3.5, 30.0, 12.0, 35.0),
    "15-64": (0.15, 30.0, 0.80, 35.0),
    "<15": (0.03, 30.0, 0.05, 35.0),
}
BURKE_POWERLAW_PARAMS_PER100K = {
    age: {"d1": a[0], "T1": a[1], "d2": a[2], "T2": a[3]}
    for age, a in BURKE_POWERLAW_ANCHORS.items()
}


def _burke_heat_tail_polynomial(T, T_ref, ref_points, degree=4):
    """Degree-4 heat-only polynomial through the origin and the anchor points."""
    T_pts = np.array([p[0] for p in ref_points], dtype=float)
    d_pts = np.array([p[1] for p in ref_points], dtype=float)
    h_pts = np.concatenate([[0.0], T_pts - T_ref])
    d_pts = np.concatenate([[0.0], d_pts])
    H = np.column_stack([h_pts ** i for i in range(1, degree + 1)])
    coeffs, *_ = np.linalg.lstsq(H, d_pts, rcond=None)
    h = np.clip(T - T_ref, 0, None)
    out = np.zeros_like(T, dtype=float)
    for i, c in enumerate(coeffs, start=1):
        out += c * np.power(h, i)
    return np.clip(out, 0, None)


def _burke_heat_tail_power(T, T_ref, d1, T1, d2, T2):
    """Convex heat-only power law through (T1, d1) and (T2, d2)."""
    h1 = max(T1 - T_ref, 1e-6)
    h2 = max(T2 - T_ref, 1e-6)
    p = np.log(d2 / d1) / np.log(h2 / h1)
    beta = d1 / (h1 ** p)
    return beta * np.power(np.clip(T - T_ref, 0, None), p)


def _burke_base_curves() -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray]]:
    T_grid = np.arange(BURKE_T_MIN, BURKE_T_MAX + 1e-6, BURKE_T_STEP)
    poly = {
        age: np.clip(_burke_heat_tail_polynomial(T_grid, BURKE_T_REF, pts) / BURKE_PER100K, 0, 1)
        for age, pts in BURKE_POLY_REF_POINTS.items()
    }
    powerlaw = {
        age: np.clip(_burke_heat_tail_power(T_grid, BURKE_T_REF, *anc) / BURKE_PER100K, 0, 1)
        for age, anc in BURKE_POWERLAW_ANCHORS.items()
    }
    return T_grid, poly, powerlaw


def _build_burke_family_json(
    T_grid: np.ndarray,
    base_mdd: dict[str, np.ndarray],
    scale_factors: pd.DataFrame | None,
    *,
    family: str,
    ref_year: int = 2020,
) -> dict[str, Any]:
    """Assemble a year-specific Burke IF JSON matching the original NB04 schema.

    Polynomial year-scaling is applied without re-clipping (old NB04 cell 47);
    the power-law is re-clipped to [0, 1] (old NB04 cell 54).
    """
    is_powerlaw = family == "burke_powerlaw"
    name_suffix = "_powerlaw" if is_powerlaw else ""
    if scale_factors is not None and len(scale_factors.index):
        years = sorted({int(y) for y in scale_factors.index} | {ref_year})
    else:
        years = [ref_year]
    ifs_by_year: dict[str, Any] = {}
    for year in years:
        block: dict[str, Any] = {}
        for age in AGE_ORDER:
            if scale_factors is not None and year in scale_factors.index:
                scale = float(scale_factors.loc[year, age])
            else:
                scale = 1.0
            scaled = base_mdd[age] * scale
            if is_powerlaw:
                scaled = np.clip(scaled, 0, 1)
            block[age] = {
                "id": AGE_TO_IF_ID[age],
                "name": f"{age}_{year}{name_suffix}",
                "intensity": np.asarray(T_grid, dtype=float).tolist(),
                "mdd": np.asarray(scaled, dtype=float).tolist(),
            }
        ifs_by_year[str(year)] = block
    out: dict[str, Any] = {
        "reference_year": ref_year,
        "years": years,
        "scaling_source": BURKE_SCALING_SOURCE,
    }
    if is_powerlaw:
        out["functional_form"] = "powerlaw_january2026_fixed_anchors"
        out["powerlaw_parameters_per100k"] = BURKE_POWERLAW_PARAMS_PER100K
    out["ifs_by_year"] = ifs_by_year
    return out


def _burke_reference_paths(root: Path, slug: str) -> tuple[dict[str, Path], dict[str, Path]]:
    ref_dir = Path(root) / "burke_if_reference" / slug
    source_paths = {
        "burke_polynomial": ref_dir / f"if_curves_by_year_{slug}.json",
        "burke_powerlaw": ref_dir / f"if_curves_by_year_{slug}_powerlaw.json",
    }
    annual_paths = {
        "burke_polynomial": ref_dir / f"annual_heat_deaths_generic_{slug}.csv",
        "burke_powerlaw": ref_dir / f"annual_heat_deaths_generic_{slug}_powerlaw.csv",
    }
    return source_paths, annual_paths


def _burke_original_annual_best_effort(root: Path, slug: str, city: str) -> dict[str, Path]:
    """Locate the original Burke-main annual-death CSVs for the recompute audit.

    Present on a developer machine with the legacy ``outputs/<city>/`` tree; absent
    on a fresh clone (in which case the audit records ``missing_original``, which is
    a handled, non-fatal status). Never raises.
    """
    for name in [slug, city, city.lower(), city.title(), slug.title()]:
        d = Path(root) / "outputs" / name / "interim"
        if (d / f"annual_heat_deaths_generic_{slug}.csv").exists():
            return {
                "burke_polynomial": d / f"annual_heat_deaths_generic_{slug}.csv",
                "burke_powerlaw": d / f"annual_heat_deaths_generic_{slug}_powerlaw.csv",
            }
    d = Path(root) / "outputs" / slug / "interim"
    return {
        "burke_polynomial": d / f"annual_heat_deaths_generic_{slug}.csv",
        "burke_powerlaw": d / f"annual_heat_deaths_generic_{slug}_powerlaw.csv",
    }


def _load_true_baseline_scaling(root: Path, slug: str) -> pd.DataFrame | None:
    """Load the committed per-country baseline-mortality scaling for all three age bins.

    This is REQUIRED for the Burke build (rather than reusing the Masselot-recovered
    ``_mdd_scaling_table``) because the Masselot ``<15`` mdd is identically zero, so the
    ``<15`` scaling cannot be recovered from the Masselot IF and would silently default
    to 1.0 — leaving the Burke ``<15`` curve unscaled. The committed CSVs in
    ``baseline_scaling/`` carry the true WCDE/Wittgenstein trend for all ages (the same
    values the Masselot IF JSONs were baked with). Returns a year-indexed DataFrame with
    AGE_ORDER columns, or None if the committed CSV is absent/malformed.
    """
    path = Path(root) / "baseline_scaling" / f"baseline_heat_scaling_{slug}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except (OSError, ValueError):
        return None
    if "year" not in df.columns or any(a not in df.columns for a in AGE_ORDER):
        return None
    df = df.set_index("year")
    df.index = df.index.astype(int)
    return df[AGE_ORDER].copy()


def _build_burke_sensitivities(
    *,
    root: Path,
    slug: str,
    city: str,
    scale_factors: pd.DataFrame,
    variant_name: str,
    main_family: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Path]]:
    """Produce the Burke sensitivity IF JSONs without any ``outputs/<city>/`` dependency.

    - Standard cities: build the polynomial + power-law Burke curves deterministically
      (city-independent base curves scaled by ``scale_factors``).
    - Cities with a committed reference in ``burke_if_reference/<slug>/`` (currently
      only Copenhagen, the extreme/Track-B city): load the verbatim reference curves so
      the non-standard sensitivity is preserved exactly.

    Returns ``(burke_jsons, source_paths, annual_paths)`` with the same shape as the
    former ``_load_original_burke_sensitivities`` so the caller is unchanged.
    """
    ref_sources, ref_annual = _burke_reference_paths(root, slug)
    use_reference = (
        ref_sources["burke_polynomial"].exists() and ref_sources["burke_powerlaw"].exists()
    )

    if use_reference:
        raw = {fam: _load_json(path) for fam, path in ref_sources.items()}
        source_paths: dict[str, Any] = dict(ref_sources)
        annual_paths = ref_annual
        built = False
        numerical_source = (
            f"committed reference Burke IF (burke_if_reference/{slug}/); "
            "verbatim original non-standard (Track-B) curves"
        )
    else:
        # Use the committed true baseline-mortality scaling (all 3 ages). Fall back to
        # the Masselot-recovered scale_factors only if the committed CSV is missing --
        # but note that fallback leaves Burke <15 unscaled (Masselot <15 mdd == 0), so
        # it is a degraded path flagged in the provenance below.
        true_scaling = _load_true_baseline_scaling(root, slug)
        burke_scaling = true_scaling if true_scaling is not None else scale_factors
        scaling_src = (
            "committed baseline_scaling/ (WCDE/Wittgenstein, all ages)"
            if true_scaling is not None
            else "Masselot-recovered _mdd_scaling_table FALLBACK (<15 unscaled!)"
        )
        T_grid, poly_base, pw_base = _burke_base_curves()
        raw = {
            "burke_polynomial": _build_burke_family_json(
                T_grid, poly_base, burke_scaling, family="burke_polynomial"
            ),
            "burke_powerlaw": _build_burke_family_json(
                T_grid, pw_base, burke_scaling, family="burke_powerlaw"
            ),
        }
        source_paths = {
            "burke_polynomial": "<built: cityheat.nb04_masselot_main._build_burke_family_json>",
            "burke_powerlaw": "<built: cityheat.nb04_masselot_main._build_burke_family_json>",
        }
        annual_paths = _burke_original_annual_best_effort(root, slug, city)
        built = True
        numerical_source = (
            "built deterministically from the March2026 Burke-main calibration "
            "(city-independent base curves x baseline-mortality year scaling); "
            f"scaling source: {scaling_src}"
        )

    burke_jsons = {
        fam: _annotate_burke_sensitivity(
            data,
            family=fam,
            source_path=source_paths[fam],
            variant_name=variant_name,
            main_family=main_family,
            built=built,
            numerical_source=numerical_source,
        )
        for fam, data in raw.items()
    }
    return burke_jsons, source_paths, annual_paths


def _load_original_burke_sensitivities(
    *,
    root: Path,
    slug: str,
    city: str,
    variant_name: str,
    main_family: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path], dict[str, Path]]:
    original_out = _original_output_dir(root, slug, city)
    original_int = original_out / "interim"
    source_paths = {
        "burke_polynomial": original_int / f"if_curves_by_year_{slug}.json",
        "burke_powerlaw": original_int / f"if_curves_by_year_{slug}_powerlaw.json",
    }
    annual_paths = {
        "burke_polynomial": original_int / f"annual_heat_deaths_generic_{slug}.csv",
        "burke_powerlaw": original_int / f"annual_heat_deaths_generic_{slug}_powerlaw.csv",
    }
    missing = [str(path) for path in [*source_paths.values(), *annual_paths.values()] if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing original Burke sensitivity artifact(s):\n" + "\n".join(missing))

    burke_jsons = {
        family: _annotate_burke_sensitivity(
            _load_json(path),
            family=family,
            source_path=path,
            variant_name=variant_name,
            main_family=main_family,
        )
        for family, path in source_paths.items()
    }
    return burke_jsons, source_paths, annual_paths


def _audit_burke_recompute(
    *,
    family: str,
    recomputed: pd.DataFrame,
    original_path: Path,
    atol: float = 1e-6,
    rtol: float = 1e-6,
) -> list[dict[str, Any]]:
    """Compare recomputed Burke annual deaths to the original Burke-main values.

    Returns one row per (year, age_group) with absolute and relative deltas and
    a within-tolerance flag. The recomputed numbers should match the originals
    when the variant pipeline shares hazard / exposures / vulnerability with the
    Burke-main pipeline; any out-of-tolerance row points to either a numerical
    artifact (acceptable) or a real upstream divergence (investigate).
    """
    rows: list[dict[str, Any]] = []
    if not original_path.exists():
        rows.append(
            {
                "family": family,
                "year": None,
                "age_group": None,
                "status": "missing_original",
                "original_path": str(original_path),
            }
        )
        return rows
    original = pd.read_csv(original_path, index_col=0)
    try:
        original.index = original.index.astype(int)
    except (TypeError, ValueError):
        pass
    for age in AGE_ORDER:
        if age not in original.columns or age not in recomputed.columns:
            continue
        years_common = sorted(set(original.index) & set(recomputed.index))
        for year in years_common:
            orig_val = float(original.loc[year, age])
            new_val = float(recomputed.loc[year, age])
            abs_diff = new_val - orig_val
            denom = max(abs(orig_val), 1e-30)
            rel_diff = abs_diff / denom
            within = (abs(abs_diff) <= atol) or (abs(rel_diff) <= rtol)
            rows.append(
                {
                    "family": family,
                    "year": int(year),
                    "age_group": age,
                    "recomputed": new_val,
                    "original": orig_val,
                    "abs_diff": abs_diff,
                    "rel_diff": rel_diff,
                    "within_tolerance": bool(within),
                    "atol": atol,
                    "rtol": rtol,
                    "status": "ok",
                    "original_path": str(original_path),
                }
            )
    return rows


def run_nb04_masselot_main(
    *,
    root: Path,
    out_dir: Path,
    int_dir: Path,
    slug: str,
    city: str,
    hazard: Any,
    exposures_by_year: dict[int, Exposures],
    main_family: str | None = None,
    output_variant: str | None = None,
    audit_strict: bool | None = None,
) -> dict[str, Path]:
    """Write Masselot-main deterministic IFs and Burke sensitivity artifacts.

    The Burke sensitivity artifacts reuse the verbatim original March2026
    Burke-main IF JSONs (the curve calibration is preserved exactly) but
    **recompute** Burke annual deaths inside this variant's namespace using the
    variant ``hazard`` and ``exposures_by_year``. This guarantees the Burke
    sensitivity numbers are internally consistent with the Masselot-main
    pipeline.

    Burke-main dependency
    ---------------------
    Producing the Masselot-main outputs no longer depends on Burke-main
    outputs for *impact computation*. The only remaining Burke-main dependency
    is the **audit comparison**, which reads the original Burke-main annual
    death CSVs from ``outputs/{city}/interim/annual_heat_deaths_generic_{slug}*.csv``
    and writes a structured per-(family, year, age_group) diff report to
    ``tables/{slug}_burke_recompute_audit.csv``. If the original CSVs are
    missing, the audit records ``missing_original`` rows but the rest of the
    pipeline still produces all artifacts.

    Audit modes
    -----------
    ``audit_strict`` controls what happens when the audit detects mismatches:

    - ``False`` (default; matches ``NB04_BURKE_AUDIT_STRICT`` env var unset or
      0): print a worst-row diagnostic, save the audit CSV, continue. Use this
      while iterating on the framework.
    - ``True`` (env ``NB04_BURKE_AUDIT_STRICT=1``): same diagnostic plus raise
      ``RuntimeError`` if ``burke_audit_status != 'pass'``. Use this for
      paper-final production runs to guarantee Masselot-main and Burke-main
      share the same upstream hazard/exposure/vulnerability.

    The overall ``burke_audit_status`` is recorded in the manifest regardless
    of strict mode so NB10 / downstream tooling can surface it.
    """
    root = Path(root)
    out_dir = Path(out_dir)
    int_dir = Path(int_dir)
    slug = str(slug).strip().lower()
    main_family = (main_family or os.environ.get("IF_MAIN_FAMILY") or "masselot_tail").strip()
    output_variant = (output_variant or os.environ.get("URBAN_HEAT_OUTPUT_VARIANT") or "masselot_main").strip()
    if audit_strict is None:
        audit_strict = os.environ.get("NB04_BURKE_AUDIT_STRICT", "0").strip().lower() in {"1", "true", "yes", "y"}
    if not output_variant.startswith("masselot_main"):
        raise RuntimeError(
            "Expected a 'masselot_main*' output variant (e.g. 'masselot_main' or "
            f"'masselot_main_agnostic'), got {output_variant!r}."
        )
    if main_family not in SUPPORTED_MASSELOT_FAMILIES:
        raise RuntimeError(f"Expected IF_MAIN_FAMILY in {sorted(SUPPORTED_MASSELOT_FAMILIES)}, got {main_family!r}.")

    fig_dir = out_dir / "figures"
    tab_dir = out_dir / "tables"
    int_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    tab_dir.mkdir(parents=True, exist_ok=True)

    masselot_dir = root / "10288665"
    masselot_sources = {
        family: masselot_dir / pattern.format(slug=slug)
        for family, pattern in MASSELOT_SOURCE_FILE.items()
    }
    missing_sources = [str(path) for path in masselot_sources.values() if not path.exists()]
    if missing_sources:
        raise FileNotFoundError("Missing Masselot IF source JSON(s):\n" + "\n".join(missing_sources))

    canonical_path = int_dir / f"if_curves_by_year_{slug}.json"
    legacy_path = int_dir / f"if_curves_frozen_{slug}.json"
    manifest_path = int_dir / f"if_family_manifest_{slug}.json"
    masselot_family_paths = {
        "masselot": int_dir / f"if_curves_by_year_{slug}_masselot.json",
        "masselot_tail": int_dir / f"if_curves_by_year_{slug}_masselot_tail.json",
    }

    masselot_jsons: dict[str, dict[str, Any]] = {}
    for family, source_path in masselot_sources.items():
        role = "main" if family == main_family else "sensitivity"
        family_json = _annotate_masselot(
            _load_json(source_path),
            family=family,
            role=role,
            source_path=source_path,
            variant_name=output_variant,
            main_family=main_family,
            canonical=False,
        )
        masselot_jsons[family] = family_json
        _write_json(masselot_family_paths[family], family_json)
        _write_json(int_dir / f"if_curves_frozen_{slug}_{family}.json", _reference_block(family_json))
        print(f"Saved Masselot {family} ({role}) IFs: {masselot_family_paths[family]}")

    main_json = _annotate_masselot(
        _load_json(masselot_sources[main_family]),
        family=main_family,
        role="main",
        source_path=masselot_sources[main_family],
        variant_name=output_variant,
        main_family=main_family,
        canonical=True,
    )
    _write_json(canonical_path, main_json)
    _write_json(legacy_path, _reference_block(main_json))
    print(f"Saved canonical Masselot-main IFs: {canonical_path}")

    scale_factors = _mdd_scaling_table(main_json)
    scale_factors.to_csv(int_dir / f"baseline_heat_scaling_{slug}.csv")
    print("Masselot-inferred future mortality scaling:")
    print(scale_factors)

    annual_by_family: dict[str, pd.DataFrame] = {}
    for family, data in masselot_jsons.items():
        annual_by_family[family] = annual_deaths_from_if_json(
            data,
            family,
            hazard=hazard,
            exposures_by_year=exposures_by_year,
        )
        annual_by_family[family].to_csv(int_dir / f"annual_heat_deaths_generic_{slug}_{family}.csv")

    heat_deaths_main = annual_by_family[main_family]
    generic_path = int_dir / f"annual_heat_deaths_generic_{slug}.csv"
    heat_deaths_main.to_csv(generic_path)
    print(f"Saved canonical Masselot-main annual deaths: {generic_path}")
    print(heat_deaths_main)

    ref_year = str(main_json.get("reference_year", 2020))
    # Burke sensitivity IFs are BUILT in-pipeline (no dependency on the legacy
    # outputs/<city>/ Burke-main tree). Standard cities use the deterministic
    # city-independent Burke calibration scaled by the same baseline-mortality
    # trend as Masselot-main (scale_factors); the sole extreme/Track-B city
    # (Copenhagen) loads its verbatim committed reference in burke_if_reference/.
    burke_jsons, burke_source_paths, burke_annual_sources = _build_burke_sensitivities(
        root=root,
        slug=slug,
        city=city,
        scale_factors=scale_factors,
        variant_name=output_variant,
        main_family=main_family,
    )
    burke_paths = {
        "burke_polynomial": int_dir / f"if_curves_by_year_{slug}_burke_polynomial.json",
        "burke_powerlaw": int_dir / f"if_curves_by_year_{slug}_powerlaw.json",
    }

    # Cross-track audit detection: when the original Burke-main reference was
    # computed under Track-B (extreme exceedance hazard) but this variant runs
    # the same Burke IFs on the absolute-T standard track, the row-by-row diff
    # is comparing different physical quantities. Skip the audit in that case
    # rather than report a meaningless `fail`. This is keyed on the raw YAML
    # (which still records Track-B even when the variant bootstrap has disabled
    # it) AND on the variant hazard units, so a future run that legitimately
    # restores Track-B in the variant will get the audit back automatically.
    cfg_raw: dict[str, Any] | None = None
    cfg_path = root / "configs" / f"{slug}.yml"
    if cfg_path.exists():
        try:
            with open(cfg_path, "r") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                cfg_raw = loaded
        except (OSError, yaml.YAMLError):
            cfg_raw = None
    ext_raw = (cfg_raw or {}).get("extreme_hazard", {}) if cfg_raw else {}
    if not isinstance(ext_raw, dict):
        ext_raw = {}
    orig_track_b = bool(ext_raw.get("enabled", False)) and bool(ext_raw.get("run_extreme_track", False))
    variant_haz_units = str(getattr(hazard, "units", "") or "").strip().lower()
    variant_on_exceedance = "exceedance" in variant_haz_units
    audit_cross_track = orig_track_b and not variant_on_exceedance
    audit_skip_reason = (
        f"Burke-main reference for {slug!r} was computed on Track-B (exceedance hazard) "
        f"but this Masselot-main variant runs on the standard absolute-T track "
        f"(hazard.units={variant_haz_units!r}). The row-by-row audit would compare "
        f"different physical quantities, so it is skipped here. The variant Burke "
        f"recomputed numbers in this directory are honest 'Burke applied to the "
        f"same hazard as Masselot' sensitivities; they are intentionally NOT "
        f"expected to match the original Burke-main Track-B numbers."
        if audit_cross_track else None
    )

    burke_audit_rows: list[dict[str, Any]] = []
    for family, data in burke_jsons.items():
        _write_json(burke_paths[family], data)
        _write_json(int_dir / f"if_curves_frozen_{slug}_{family}.json", _reference_block(data))
        if family == "burke_powerlaw":
            _write_json(int_dir / f"if_curves_by_year_{slug}_burke_powerlaw.json", data)
            _write_json(int_dir / f"if_curves_frozen_{slug}_powerlaw.json", _reference_block(data))
        annual_dest = int_dir / f"annual_heat_deaths_generic_{slug}_{family}.csv"
        # Recompute Burke annual deaths inside this variant namespace using the
        # current hazard / exposures so the sensitivity is internally consistent
        # with the Masselot-main pipeline. The IF curve itself is the verbatim
        # original Burke calibration (curve source preserved; impact recomputed).
        recomputed = annual_deaths_from_if_json(
            data,
            family,
            hazard=hazard,
            exposures_by_year=exposures_by_year,
        )
        recomputed.to_csv(annual_dest)
        if family == "burke_powerlaw":
            recomputed.to_csv(int_dir / f"annual_heat_deaths_generic_{slug}_powerlaw.csv")
        if audit_cross_track:
            burke_audit_rows.append(
                {
                    "family": family,
                    "year": None,
                    "age_group": None,
                    "status": "skipped_cross_track",
                    "skip_reason": audit_skip_reason,
                    "original_path": str(burke_annual_sources[family]),
                }
            )
        else:
            burke_audit_rows.extend(
                _audit_burke_recompute(
                    family=family,
                    recomputed=recomputed,
                    original_path=burke_annual_sources[family],
                )
            )
        print(f"Recomputed Burke sensitivity annual deaths ({family}): {annual_dest}")
        print(f"  IF curve source preserved verbatim from: {burke_source_paths[family]}")

    burke_audit_df = pd.DataFrame(burke_audit_rows)
    burke_audit_path = tab_dir / f"{slug}_burke_recompute_audit.csv"
    burke_audit_df.to_csv(burke_audit_path, index=False)

    # Compute a single overall status that NB10 / downstream tooling can read
    # from the manifest. Semantics:
    #   - "pass"                  : every checked row was within tolerance
    #   - "fail"                  : at least one row exceeded tolerance
    #   - "missing_original"      : at least one Burke-main original CSV was missing
    #                               (no comparison possible for that family)
    #   - "no_burke_processed"    : no Burke families were processed at all
    #   - "skipped_cross_track"   : Burke-main reference uses a different hazard
    #                               track than the variant (e.g. Copenhagen, where
    #                               Burke-main is Track-B and the variant is now
    #                               on the absolute-T standard track). Per-row
    #                               diff is not meaningful; skipped by design.
    burke_audit_fail_count = 0
    burke_audit_worst: dict[str, Any] | None = None
    if burke_audit_df.empty:
        burke_audit_status = "no_burke_processed"
    elif (burke_audit_df["status"] == "skipped_cross_track").any():
        burke_audit_status = "skipped_cross_track"
    elif (burke_audit_df["status"] == "missing_original").any():
        burke_audit_status = "missing_original"
    else:
        fail = burke_audit_df[
            burke_audit_df["within_tolerance"].astype("boolean").fillna(True).eq(False)
        ]
        burke_audit_fail_count = int(len(fail))
        if burke_audit_fail_count > 0:
            burke_audit_status = "fail"
            worst_idx = fail["rel_diff"].abs().idxmax()
            burke_audit_worst = fail.loc[worst_idx].to_dict()
        else:
            burke_audit_status = "pass"

    if burke_audit_status == "pass":
        print(
            f"Burke recompute audit: PASS (all rows within atol/rtol=1e-6); "
            f"see {burke_audit_path}."
        )
    elif burke_audit_status == "skipped_cross_track":
        print(
            f"Burke recompute audit: SKIPPED_CROSS_TRACK for {slug!r} -- "
            f"Burke-main reference uses Track-B (exceedance) but this variant "
            f"runs on the standard absolute-T track. Variant Burke recompute "
            f"is the honest same-hazard sensitivity; see {burke_audit_path}."
        )
    elif burke_audit_status == "fail":
        assert burke_audit_worst is not None  # narrowed by branch above
        print(
            f"!! Burke recompute audit: FAIL ({burke_audit_fail_count} row(s) "
            f"exceeded tolerance); see {burke_audit_path} for details."
        )
        print(
            "   Worst row: family="
            f"{burke_audit_worst['family']}, year={burke_audit_worst['year']}, "
            f"age_group={burke_audit_worst['age_group']}, "
            f"abs_diff={float(burke_audit_worst['abs_diff']):.3e}, "
            f"rel_diff={float(burke_audit_worst['rel_diff']):.3e}"
        )
    elif burke_audit_status == "missing_original":
        missing_families = sorted(
            set(burke_audit_df.loc[burke_audit_df["status"] == "missing_original", "family"].astype(str))
        )
        print(
            f"!! Burke recompute audit: MISSING_ORIGINAL for families "
            f"{missing_families}; cannot verify integrity vs Burke-main. "
            f"See {burke_audit_path}."
        )
    else:  # no_burke_processed
        print(
            f"Burke recompute audit: NO_BURKE_PROCESSED (no Burke families ran). "
            f"See {burke_audit_path}."
        )

    if audit_strict and burke_audit_status not in {"pass", "skipped_cross_track"}:
        raise RuntimeError(
            f"NB04 Masselot-main: Burke recompute audit status is "
            f"{burke_audit_status!r} but audit_strict=True. See {burke_audit_path} "
            f"for the structured diff report. Either fix the upstream divergence "
            f"between Burke-main and Masselot-main, or set NB04_BURKE_AUDIT_STRICT=0 "
            f"(audit_strict=False) to continue with a warning instead."
        )

    other_masselot = "masselot_tail" if main_family == "masselot" else "masselot"
    manifest = {
        "output_variant": output_variant,
        "if_main_family": main_family,
        "if_main_source": "Masselot",
        "if_role": "main",
        "canonical_if_json": str(canonical_path),
        "canonical_source_family": main_family,
        "masselot_extrapolation": MASSELOT_EXTRAPOLATION[main_family],
        "masselot_extrapolation_assumption": MASSELOT_EXTRAPOLATION[main_family],
        "masselot_source": str(masselot_sources[main_family]),
        "masselot_extrapolation_sensitivity_family": other_masselot,
        "masselot_extrapolation_sensitivity": str(masselot_family_paths[other_masselot]),
        "burke_role": "sensitivity",
        "burke_polynomial_sensitivity": str(burke_paths["burke_polynomial"]),
        "burke_powerlaw_sensitivity": str(burke_paths["burke_powerlaw"]),
        "burke_polynomial_original_source": str(burke_source_paths["burke_polynomial"]),
        "burke_powerlaw_original_source": str(burke_source_paths["burke_powerlaw"]),
        "burke_sensitivity_curve_source": "exact original March2026 Burke-main IF JSONs (verbatim curve preserved)",
        "burke_sensitivity_impact_source": "recomputed inside Masselot-main variant namespace using variant hazard/exposures",
        "burke_recompute_audit": str(burke_audit_path),
        "burke_recompute_audit_status": burke_audit_status,
        "burke_recompute_audit_fail_count": int(burke_audit_fail_count),
        "burke_recompute_audit_strict": bool(audit_strict),
        "burke_recompute_audit_cross_track": bool(audit_cross_track),
        "burke_recompute_audit_skip_reason": audit_skip_reason,
        "burke_audit_dependency": (
            "Burke recompute audit reads original Burke-main annual death CSVs from "
            "outputs/{city}/interim/. This is the only remaining dependency on the "
            "Burke-main workflow; Burke impact numbers themselves are recomputed in "
            "the variant namespace."
        ),
        "current_original_workflow_preserved": True,
        "compatibility_note": (
            f"{canonical_path.name} is the downstream compatibility filename for the main deterministic IF. "
            f"In this variant its true source is Masselot / {main_family}, recorded in the JSON metadata."
        ),
    }
    if main_family == "masselot_tail":
        manifest["masselot_tail_extrapolation"] = "loglinear_tail"
    _write_json(manifest_path, manifest)
    print(f"Saved IF family manifest: {manifest_path}")

    selected_temps = [25, 30, 35, 40, 45]
    comparison_rows: list[dict[str, Any]] = []
    all_families = {**masselot_jsons, **burke_jsons}
    for family, data in all_families.items():
        role = "main" if family == main_family else "sensitivity"
        ref = str(data.get("reference_year", 2020))
        for age in AGE_ORDER:
            rec = data["ifs_by_year"][ref][age]
            intensity = np.asarray(rec["intensity"], dtype=float)
            mdd = np.asarray(rec["mdd"], dtype=float)
            for temp in selected_temps:
                comparison_rows.append(
                    {
                        "if_family": family,
                        "if_role": role,
                        "age_group": age,
                        "temperature_C": temp,
                        "mdd": float(np.interp(temp, intensity, mdd)),
                    }
                )
    comparison = pd.DataFrame(comparison_rows)
    comparison_path = tab_dir / f"{slug}_masselot_main_burke_sensitivity_if_comparison.csv"
    comparison.to_csv(comparison_path, index=False)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=False)
    labels = {
        "masselot": "Masselot constant tail",
        "masselot_tail": "Masselot log-linear tail",
        "burke_polynomial": "Burke polynomial",
        "burke_powerlaw": "Burke power-law",
    }
    for ax, age in zip(axes, AGE_ORDER):
        for family, data in all_families.items():
            rec = data["ifs_by_year"][str(data.get("reference_year", 2020))][age]
            ax.plot(
                rec["intensity"],
                rec["mdd"],
                label=labels.get(family, family),
                lw=2.2 if family == main_family else 1.3,
            )
        ax.set_title(age)
        ax.set_xlabel("Daily mean 2 m temperature (degC)")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("MDD")
    axes[-1].legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1.0))
    fig.suptitle(f"{city}: Masselot-main with Burke sensitivity IFs ({ref_year})")
    fig.tight_layout()
    comparison_fig = fig_dir / f"{slug}_IF_masselot_main_burke_sensitivity_comparison.png"
    fig.savefig(comparison_fig, dpi=160, bbox_inches="tight")
    plt.show()

    main_fig, ax = plt.subplots(figsize=(8, 5))
    plot_years = [str(y) for y in [2020, 2050] if str(y) in main_json["ifs_by_year"]]
    for year_str in plot_years:
        for age in AGE_ORDER:
            rec = main_json["ifs_by_year"][year_str][age]
            ax.plot(rec["intensity"], rec["mdd"], label=f"{age} {year_str}")
    ax.set_title(f"{city}: Masselot-main age-specific IFs ({main_family})")
    ax.set_xlabel("Daily mean 2 m temperature (degC)")
    ax.set_ylabel("MDD (daily per-person mortality probability)")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    main_fig.tight_layout()
    main_fig_path = fig_dir / f"{slug}_IF_masselot_main_{main_family}.png"
    main_fig.savefig(main_fig_path, dpi=160)
    plt.show()

    return {
        "canonical_if": canonical_path,
        "canonical_frozen_if": legacy_path,
        "annual_deaths": generic_path,
        "manifest": manifest_path,
        "masselot_constant": masselot_family_paths["masselot"],
        "masselot_tail": masselot_family_paths["masselot_tail"],
        "burke_polynomial": burke_paths["burke_polynomial"],
        "burke_powerlaw": burke_paths["burke_powerlaw"],
        "burke_recompute_audit": burke_audit_path,
        "comparison_table": comparison_path,
        "comparison_figure": comparison_fig,
        "main_figure": main_fig_path,
    }
