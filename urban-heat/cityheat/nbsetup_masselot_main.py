"""Variant-only bootstrap helpers for the March2026 Masselot-main notebooks.

This module is intentionally separate from ``cityheat.nbsetup`` so the
historical Burke-main March2026 workflow keeps using the original helpers and
``outputs/<city>`` paths.
"""

from __future__ import annotations

from pathlib import Path
import os
import re

import yaml

from .nbsetup import find_repo_root


DEFAULT_OUTPUT_VARIANT = "masselot_main"
DEFAULT_IF_MAIN_FAMILY = "masselot_tail"
_VARIANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_IF_FAMILIES = {"masselot", "masselot_tail", "burke_polynomial", "burke_powerlaw"}


def get_output_variant(default: str = DEFAULT_OUTPUT_VARIANT) -> str:
    """Return the explicit Masselot-main output variant namespace."""
    variant = os.environ.get("URBAN_HEAT_OUTPUT_VARIANT", default).strip() or default
    if not _VARIANT_RE.fullmatch(variant):
        raise ValueError(
            "URBAN_HEAT_OUTPUT_VARIANT must contain only letters, digits, dots, "
            "underscores, or hyphens, and must start with a letter or digit."
        )
    return variant


def get_if_main_family(default: str = DEFAULT_IF_MAIN_FAMILY) -> str:
    """Return the Masselot-main deterministic IF family."""
    family = os.environ.get("IF_MAIN_FAMILY", default).strip() or default
    if family not in _IF_FAMILIES:
        raise ValueError(f"Unsupported IF_MAIN_FAMILY={family!r}; expected one of {sorted(_IF_FAMILIES)}.")
    return family


def masselot_extrapolation(if_family: str | None = None) -> str | None:
    family = if_family or get_if_main_family()
    return {
        "masselot": "constant_tail",
        "masselot_tail": "loglinear_tail",
    }.get(family)


def if_main_source(if_family: str | None = None) -> str:
    family = if_family or get_if_main_family()
    return "Masselot" if family.startswith("masselot") else "Burke"


def resolve_outputs_root(project: Path, variant: str | None = None) -> Path:
    project = Path(project)
    return project / "outputs_variants" / (variant or get_output_variant())


def resolve_city_output(project: Path, city: str, variant: str | None = None) -> Path:
    return resolve_outputs_root(project, variant) / str(city).strip().lower()


_EXTREME_TRACK_TOKENS = {"extreme", "event", "track_b", "heatwave"}


def enforce_masselot_main_track(cfg: dict) -> dict:
    """Strip cold-city Track-B from cfg and env for the Masselot-main variant.

    Track-B was a Burke-era workaround for the fixed 20 degC T_ref producing zero
    heat impacts in cold cities (notably Copenhagen). Masselot's per-city MMT
    (~18.4 degC for Copenhagen 65+) removes that need: heat-attributable mortality
    is well-defined above the local MMT on the standard daily-mean track. Running
    Track-B in this variant would also surface the exceedance-to-absolute hazard
    shift gap in nb04_masselot_main.py (cf. memory/copenhagen_nb04_extreme_track_bug.md).

    The original Burke-main March2026 workflow keeps Track-B intact because it
    imports cityheat.nbsetup (not this module). Mutates cfg in place and returns
    it for chaining.

    Also unblocks ``extreme_hazard.standard_track.run_policies`` when the city's
    config had set it to ``False``. In the Burke-main era that flag was used to
    route policy runs (NB05 AC, NB06 EWS, NB07 vegetation, NB08 CBA) exclusively
    through Track-B for Copenhagen; in Masselot-main those policies must run on
    the standard track instead, so the standard-track gate is opened here.
    """
    ext_cfg = cfg.get("extreme_hazard")
    if isinstance(ext_cfg, dict) and ext_cfg.get("enabled"):
        ext_cfg["enabled"] = False
        ext_cfg["run_extreme_track"] = False
        ext_cfg["disabled_by"] = "nbsetup_masselot_main.enforce_masselot_main_track"
        std_cfg = ext_cfg.get("standard_track")
        if isinstance(std_cfg, dict) and std_cfg.get("run_policies") is False:
            std_cfg["run_policies"] = True
            std_cfg["run_policies_overridden_by"] = (
                "nbsetup_masselot_main.enforce_masselot_main_track"
            )
    haz_track_env = os.environ.get("HAZARD_TRACK", "").strip().lower()
    if haz_track_env in _EXTREME_TRACK_TOKENS:
        os.environ["HAZARD_TRACK"] = "standard"
    return cfg


def bootstrap(city: str) -> dict[str, object]:
    PROJECT = find_repo_root()
    CFG = PROJECT / "configs" / f"{city.lower()}.yml"
    if not CFG.exists():
        raise FileNotFoundError(f"Config not found: {CFG}")

    with open(CFG, "r") as f:
        cfg = yaml.safe_load(f)

    enforce_masselot_main_track(cfg)

    city_name = str(cfg.get("city_name", city))
    os.environ["CITY"] = city_name

    wp_iso3 = cfg.get("wp_iso3")
    if wp_iso3:
        os.environ["WP_ISO3"] = str(wp_iso3).upper()

    wp_country = cfg.get("wp_country")
    if wp_country:
        os.environ["WP_COUNTRY"] = str(wp_country)

    output_variant = get_output_variant()
    if_main_family = get_if_main_family()

    OUT = resolve_city_output(PROJECT, city, output_variant)
    OUT.mkdir(parents=True, exist_ok=True)
    INT = OUT / "interim"
    INT.mkdir(parents=True, exist_ok=True)

    BASE = PROJECT / cfg.get("base_dir", f"data/{city}")
    p_LCZ = BASE / "LCZ"
    p_UrbClim = BASE / "UrbClim"
    p_GVI = BASE / "gvi"
    p_CoolEff = BASE / "CoolingEff"

    return {
        "PROJECT": PROJECT,
        "CITY": city_name,
        "CFG": CFG,
        "cfg": cfg,
        "OUT": OUT,
        "INT": INT,
        "BASE": BASE,
        "OUTPUT_VARIANT": output_variant,
        "OUTPUT_ROOT": resolve_outputs_root(PROJECT, output_variant),
        "IF_MAIN_FAMILY": if_main_family,
        "IF_MAIN_SOURCE": if_main_source(if_main_family),
        "MASSELOT_EXTRAPOLATION": masselot_extrapolation(if_main_family),
        "p_LCZ": p_LCZ,
        "p_UrbClim": p_UrbClim,
        "p_GVI": p_GVI,
        "p_CoolEff": p_CoolEff,
    }
