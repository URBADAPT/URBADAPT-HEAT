"""Variant-only NB10 summary wrapper for March2026 Masselot-main notebooks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import nb10_summary as _base
from .nbsetup_masselot_main import (
    get_if_main_family,
    get_output_variant,
    if_main_source,
    masselot_extrapolation,
    resolve_city_output,
)


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


_ORIGINAL_BUILD_SUMMARY_METRICS = None


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


def _build_summary_metrics(sp: _base.SummaryPaths, cfg: dict[str, Any]) -> _base.pd.DataFrame:
    base_builder = _ORIGINAL_BUILD_SUMMARY_METRICS or _base._build_summary_metrics
    metrics = base_builder(sp, cfg)
    if_main_family = get_if_main_family()
    rows = [
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

    # Surface the NB04 Burke recompute audit status so this single CSV is
    # sufficient to tell whether the variant Burke sensitivity numbers were
    # internally consistent with the original Burke-main pipeline. Fields
    # mirror what nb04_masselot_main.run_nb04_masselot_main writes to
    # if_family_manifest_{slug}.json.
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

    # Surface the NB09 headline LHS scope label so NB10 records what the
    # headline uncertainty band actually represents (Masselot headline vs.
    # full LHS vs. Burke-conditional sensitivity). The Burke-conditional
    # runs live under a different unc_dir (tables/uncertainty_burke_sensitivity)
    # and are intentionally NOT surfaced here — this block describes the
    # *headline* run that NB10's figures draw from.
    nb09_meta = _safe_load_json(sp.uq_dir / f"uq_dimensions_{sp.slug}_improved_fast.json")
    if nb09_meta is not None:
        lhs_scope = nb09_meta.get("lhs_scope")
        if lhs_scope is not None:
            rows.append(
                {
                    "section": "run_config",
                    "metric": "lhs_scope",
                    "value": str(lhs_scope),
                    "unit": "",
                }
            )
        lhs_run_type = nb09_meta.get("lhs_run_type")
        if lhs_run_type is not None:
            rows.append(
                {
                    "section": "run_config",
                    "metric": "lhs_run_type",
                    "value": str(lhs_run_type),
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

    run_config = _base.pd.DataFrame(rows)
    return _base.pd.concat([run_config, metrics], ignore_index=True)


def run_nb10_summary(
    city: str | None = None,
    sections: list[str] | None = None,
    verbose: bool = True,
) -> dict[str, list[Path]]:
    global _ORIGINAL_BUILD_SUMMARY_METRICS
    original_build_paths = _base._build_paths
    original_build_summary_metrics = _base._build_summary_metrics
    try:
        _ORIGINAL_BUILD_SUMMARY_METRICS = original_build_summary_metrics
        _base._build_paths = _build_paths
        _base._build_summary_metrics = _build_summary_metrics
        if verbose:
            print(f"Output variant: {get_output_variant()}")
            print(f"IF main family: {get_if_main_family()}")
        return _base.run_nb10_summary(city=city, sections=sections, verbose=verbose)
    finally:
        _base._build_paths = original_build_paths
        _base._build_summary_metrics = original_build_summary_metrics
        _ORIGINAL_BUILD_SUMMARY_METRICS = None
