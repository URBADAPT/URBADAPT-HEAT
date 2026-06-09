"""Variant-only NB09 fast runner for March2026 Masselot-main notebooks.

The original ``cityheat.nb09_improved_fast`` module remains the Burke-main
workflow implementation. This wrapper only changes the output namespace and IF
family ordering for notebooks that explicitly import this module.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import yaml

from .nbsetup_masselot_main import (
    enforce_masselot_main_track,
    find_repo_root,
    get_if_main_family,
    get_output_variant,
    if_main_source,
    masselot_extrapolation,
    resolve_city_output,
    resolve_outputs_root,
)

_DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_ROOT = resolve_outputs_root(_DEFAULT_RUNTIME_ROOT)
os.environ.setdefault("MPLCONFIGDIR", str(_DEFAULT_OUTPUT_ROOT / ".mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(_DEFAULT_OUTPUT_ROOT / ".cache"))

from . import nb09_improved_fast as _base


IF_FAMILY_ORDER_BY_MAIN = {
    "burke_polynomial": ["burke_polynomial", "burke_powerlaw", "masselot", "masselot_tail"],
    "burke_powerlaw": ["burke_powerlaw", "burke_polynomial", "masselot", "masselot_tail"],
    "masselot_tail": ["masselot_tail", "masselot", "burke_polynomial", "burke_powerlaw"],
    "masselot": ["masselot", "masselot_tail", "burke_polynomial", "burke_powerlaw"],
}


def _ensure_runtime_dirs(root: Path) -> None:
    output_root = resolve_outputs_root(root)
    os.environ.setdefault("MPLCONFIGDIR", str(output_root / ".mpl"))
    os.environ.setdefault("XDG_CACHE_HOME", str(output_root / ".cache"))
    (output_root / ".mpl").mkdir(parents=True, exist_ok=True)
    (output_root / ".cache").mkdir(parents=True, exist_ok=True)


class NB09ImprovedFastMasselotMain(_base.NB09ImprovedFast):
    def __init__(self, slug: str):
        self.root = find_repo_root(Path(__file__).resolve())
        _ensure_runtime_dirs(self.root)

        self.slug = str(slug).strip().lower()
        self.cfg_path = self.root / "configs" / f"{self.slug}.yml"
        if not self.cfg_path.exists():
            raise FileNotFoundError(f"Missing config: {self.cfg_path}")

        with open(self.cfg_path, "r") as f:
            self.cfg = yaml.safe_load(f)

        # Strip cold-city Track-B before _configure_hazard_track reads cfg.
        # Masselot's per-city MMT makes Copenhagen behave like the warm cities
        # on the standard daily-mean track; keeping Track-B would re-introduce
        # the exceedance-encoding mismatch that the headline LHS is supposed
        # to avoid. See cityheat.nbsetup_masselot_main.enforce_masselot_main_track.
        enforce_masselot_main_track(self.cfg)

        self.city = self.cfg.get("city_name", self.slug.title())
        base_dir_cfg = self.cfg.get("base_dir")
        if base_dir_cfg:
            self.base = (self.root / str(base_dir_cfg)).resolve()
        else:
            self.base = self.root / "data" / self.slug
        self.base_dir = self.base

        self.output_variant = get_output_variant()
        self.if_main_family = get_if_main_family()
        if self.if_main_family not in _base.IF_FAMILIES:
            raise ValueError(
                f"Unsupported IF_MAIN_FAMILY={self.if_main_family!r}; "
                f"expected one of {_base.IF_FAMILIES}."
            )
        self.out = resolve_city_output(self.root, self.slug, self.output_variant)
        self.int_dir = self.out / "interim"
        self.tab_dir = self.out / "tables"
        self.unc_dir = self.tab_dir / "uncertainty_improved_fast"
        self.unc_dir.mkdir(parents=True, exist_ok=True)

        self.exp_cache: dict[str, _base.Exposures] = {}
        self.exp_age_cache: dict[tuple[str, str], _base.Exposures] = {}
        self.if_block_cache: dict[tuple[str, int], dict[str, Any]] = {}

        self._configure_hazard_track()
        self._load_core_artifacts()
        self._load_hazard_scaffold()
        self._load_climate_inputs()
        self._load_exposure_inputs()
        self._load_ac_inputs()
        self._load_ews_inputs()
        self._load_tree_inputs()
        self._load_vulnerability_baseline()
        self._build_param_specs()

        # Preserve the full available-family set so set_lhs_scope() can validate
        # subset requests later without re-running file discovery.
        self._full_available_if_families: list[str] = list(self.available_if_families)
        self._default_unc_dir: Path = self.unc_dir
        self._lhs_scope: str = "full"
        self._lhs_scope_family: str | None = None

    def set_lhs_scope(
        self,
        scope: str,
        *,
        family: str | None = None,
        require_both_masselot: bool = True,
    ) -> None:
        """Restrict the headline LHS to a subset of IF families and route outputs.

        Scopes
        ------
        - ``"masselot_headline"``: sample over the Masselot families only
          (``masselot_tail`` and ``masselot``). Burke families remain available
          via the JSONs on disk but are not part of the LHS — this captures the
          Masselot extrapolation bracket inside the headline uncertainty and
          keeps the Burke/Masselot structural choice outside it.
        - ``"burke_sensitivity"``: pin the LHS to a single Burke family
          (``family`` must be ``"burke_polynomial"`` or ``"burke_powerlaw"``).
          The resulting Burke run is a **conditional LHS run** (all
          parametric uncertainty dimensions are sampled normally, with the IF
          family alone held fixed). It is NOT a deterministic point estimate.
          Outputs are written to a dedicated ``uncertainty_burke_sensitivity/{family}/``
          subfolder so they don't collide with the headline.
        - ``"full"``: restore the full available-family list (uniform LHS over
          all four). Provided for symmetry / debugging; not the recommended
          production mode for Masselot-main.

        ``require_both_masselot``
            Only consulted when ``scope == "masselot_headline"``. When True
            (default; production-safe), raises ``RuntimeError`` if either
            ``masselot`` or ``masselot_tail`` is missing from the available
            families, since both are needed to honestly bracket the
            extrapolation uncertainty in the headline. Set False to allow a
            partial headline run (only useful for debugging or for a city
            where only one Masselot variant exists).

        Calling this method rebuilds ``param_specs`` and ``problem`` so the LHS
        sampler picks up the new family set on the next ``run()``.
        """
        if scope == "full":
            families = list(self._full_available_if_families)
            sub = "uncertainty_improved_fast"
        elif scope == "masselot_headline":
            families = [f for f in self._full_available_if_families if f.startswith("masselot")]
            if not families:
                raise RuntimeError(
                    f"No Masselot families available for headline LHS on {self.slug!r}; "
                    f"have {self._full_available_if_families!r}."
                )
            if require_both_masselot:
                required = {"masselot", "masselot_tail"}
                missing = sorted(required - set(families))
                if missing:
                    raise RuntimeError(
                        f"Headline LHS for {self.slug!r} requires both Masselot families "
                        f"({sorted(required)}) but missing {missing}; "
                        f"have {self._full_available_if_families!r}. "
                        f"Run aggregate_masselot_ifs.py to produce both extrapolation "
                        f"variants, or pass require_both_masselot=False (env "
                        f"NB09_REQUIRE_BOTH_MASSELOT=0) to proceed with what is available."
                    )
            sub = "uncertainty_improved_fast"
        elif scope == "burke_sensitivity":
            if family not in {"burke_polynomial", "burke_powerlaw"}:
                raise ValueError(
                    "For scope='burke_sensitivity', family must be 'burke_polynomial' "
                    f"or 'burke_powerlaw'; got {family!r}."
                )
            if family not in self._full_available_if_families:
                raise RuntimeError(
                    f"{family!r} not available on {self.slug!r}; "
                    f"have {self._full_available_if_families!r}."
                )
            families = [family]
            sub = f"uncertainty_burke_sensitivity/{family}"
        else:
            raise ValueError(
                f"Unknown LHS scope {scope!r}; expected 'masselot_headline', "
                f"'burke_sensitivity', or 'full'."
            )

        self.available_if_families = list(families)
        self.unc_dir = self.tab_dir / sub
        self.unc_dir.mkdir(parents=True, exist_ok=True)
        # Rebuild param_specs + problem so the LHS sampler sees the new family
        # count via IF_FAMILY_IDX.
        self._build_param_specs()
        self._lhs_scope = scope
        self._lhs_scope_family = family

    def _ordered_if_families(self) -> list[str]:
        order = IF_FAMILY_ORDER_BY_MAIN.get(self.if_main_family, _base.IF_FAMILIES)
        return [family for family in order if family in _base.IF_FAMILIES]

    def _if_family_candidates(self) -> dict[str, list[Path]]:
        canonical = self.int_dir / f"if_curves_by_year_{self.slug}.json"
        burke_poly = self.int_dir / f"if_curves_by_year_{self.slug}_burke_polynomial.json"
        burke_power = self.int_dir / f"if_curves_by_year_{self.slug}_powerlaw.json"
        masselot_const = self.int_dir / f"if_curves_by_year_{self.slug}_masselot.json"
        masselot_tail = self.int_dir / f"if_curves_by_year_{self.slug}_masselot_tail.json"

        if self.if_main_family == "masselot_tail":
            return {
                "masselot_tail": [canonical, masselot_tail],
                "masselot": [masselot_const],
                "burke_polynomial": [burke_poly],
                "burke_powerlaw": [burke_power],
            }
        if self.if_main_family == "masselot":
            return {
                "masselot": [canonical, masselot_const],
                "masselot_tail": [masselot_tail],
                "burke_polynomial": [burke_poly],
                "burke_powerlaw": [burke_power],
            }
        if self.if_main_family == "burke_powerlaw":
            return {
                "burke_powerlaw": [canonical, burke_power],
                "burke_polynomial": [burke_poly],
                "masselot": [masselot_const],
                "masselot_tail": [masselot_tail],
            }
        return {
            "burke_polynomial": [canonical],
            "burke_powerlaw": [burke_power],
            "masselot": [masselot_const],
            "masselot_tail": [masselot_tail],
        }

    def _load_core_artifacts(self) -> None:
        self.template_tif = self.int_dir / "template_ref.tif"
        self.city_mask_npz = self.int_dir / "city_mask.npz"
        self.exp_manifest_path = self.int_dir / "exposure_manifest.json"
        self.if_jsons = {}
        for family, candidates in self._if_family_candidates().items():
            path = next((candidate for candidate in candidates if candidate.exists()), None)
            if path is not None:
                self.if_jsons[family] = path
        self.available_if_families = [f for f in self._ordered_if_families() if f in self.if_jsons]
        if not self.available_if_families:
            candidates = [
                str(path)
                for paths in self._if_family_candidates().values()
                for path in paths
            ]
            raise FileNotFoundError(
                "No impact-function JSONs found for the Masselot-main family setup:\n"
                + "\n".join(candidates)
            )
        if self.use_extreme_track:
            self._load_extreme_track_meta()

    def save_outputs(self, *args: Any, **kwargs: Any) -> dict[str, Path]:
        paths = super().save_outputs(*args, **kwargs)
        meta_path = paths.get("meta")
        if meta_path is not None and meta_path.exists():
            with open(meta_path, "r") as f:
                meta = json.load(f)
            # Build an explicit, machine-readable label for the run nature so
            # any downstream consumer reading this meta JSON sees what the
            # output represents without having to trace source code.
            if self._lhs_scope == "masselot_headline":
                lhs_run_type = "masselot_headline_lhs_band"
                lhs_run_interpretation = (
                    "Masselot extrapolation-bracketed LHS band: full LHS over "
                    "all parametric uncertainty dimensions, with IF family "
                    "restricted to {masselot, masselot_tail}. Captures the "
                    "within-Masselot extrapolation uncertainty inside the "
                    "headline band. Not a Burke ensemble; not a deterministic "
                    "point estimate."
                )
            elif self._lhs_scope == "burke_sensitivity":
                lhs_run_type = "burke_conditional_lhs_band"
                lhs_run_interpretation = (
                    "Burke-conditional LHS sensitivity band: full LHS over all "
                    f"parametric uncertainty dimensions with IF family pinned "
                    f"to {self._lhs_scope_family!r}. All other dimensions "
                    "(climate, demography, adaptation, costs, vulnerability, "
                    "...) are sampled normally. This is NOT a deterministic "
                    "point comparison vs the Masselot headline; it is a "
                    "conditional uncertainty band that quantifies the spread "
                    "of impacts when the structural IF choice is fixed at this "
                    "Burke family."
                )
            else:
                lhs_run_type = "full_lhs_band"
                lhs_run_interpretation = (
                    "Full LHS band over all available IF families uniformly "
                    "and all parametric uncertainty dimensions. Provided for "
                    "debugging/symmetry; not the recommended production mode."
                )
            meta.update(
                {
                    "output_variant": self.output_variant,
                    "if_main_family": self.if_main_family,
                    "if_main_source": if_main_source(self.if_main_family),
                    "masselot_extrapolation": masselot_extrapolation(self.if_main_family),
                    "if_sensitivity_families": [
                        family for family in self.available_if_families if family != self.if_main_family
                    ],
                    "if_roles": {
                        family: "main" if family == self.if_main_family else "sensitivity"
                        for family in self.available_if_families
                    },
                    "burke_role": "sensitivity" if self.if_main_family.startswith("masselot") else "main",
                    "lhs_scope": self._lhs_scope,
                    "lhs_scope_family": self._lhs_scope_family,
                    "lhs_sampled_families": list(self.available_if_families),
                    "full_available_if_families": list(self._full_available_if_families),
                    "lhs_run_type": lhs_run_type,
                    "lhs_run_interpretation": lhs_run_interpretation,
                    "current_original_workflow_preserved": True,
                }
            )
            if "masselot_tail" in self.available_if_families:
                meta["masselot_tail_extrapolation"] = "loglinear_tail"
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)
        return paths


def regenerate_saved_figures(city: str) -> Path:
    slug = city.strip().lower()
    runner = NB09ImprovedFastMasselotMain(slug)
    # Default unc_dir is the headline location; that's where the figures belong.
    samples_df = _base.pd.read_csv(runner.unc_dir / f"unc_samples_{runner.slug}_improved_fast.csv")
    sens_aai_df = _base.pd.read_csv(runner.unc_dir / f"sens_aai_agg_{runner.slug}_improved_fast.csv")
    sens_cba_df = _base.pd.read_csv(runner.unc_dir / f"sens_cba_ews_{runner.slug}_improved_fast.csv")
    sens_vuln_path = runner.unc_dir / f"sens_vulnerability_{runner.slug}_improved_fast.csv"
    sens_vuln_df = _base.pd.read_csv(sens_vuln_path) if sens_vuln_path.exists() else _base.pd.DataFrame()
    runner.make_figures(samples_df, sens_aai_df, sens_cba_df, sens_vuln_df)
    return runner.unc_dir


def run_nb09_improved_fast(
    city: str | None = None,
    n: int | None = None,
    seed: int | None = None,
    make_figures: bool | None = None,
    burke_sensitivity: bool | None = None,
    require_both_masselot: bool | None = None,
) -> dict[str, Path]:
    """Run the Masselot-main headline NB09 plus optional Burke conditional sensitivity runs.

    The **headline** LHS samples only Masselot families (``masselot_tail`` and
    ``masselot``), keeping the Masselot extrapolation bracket inside the
    headline uncertainty. Burke families are NOT part of the headline LHS.

    If ``burke_sensitivity`` is True (default; can be disabled via the
    ``NB09_BURKE_SENSITIVITY=0`` env var or the ``burke_sensitivity=False``
    kwarg), two additional NB09 runs are performed with IF_FAMILY pinned to
    ``burke_polynomial`` and ``burke_powerlaw`` respectively. These are
    **IF-conditional LHS runs**, not deterministic point estimates: all other
    parametric uncertainty dimensions (climate scenario, GCM, demography,
    adaptation, costs, vulnerability, ...) are sampled normally — only the IF
    family is held fixed. Outputs go to ``uncertainty_burke_sensitivity/{family}/``
    so they do not overwrite the headline.

    ``require_both_masselot`` (default True; env ``NB09_REQUIRE_BOTH_MASSELOT``)
    enforces that both ``masselot`` and ``masselot_tail`` are present before the
    headline run starts. Both are needed to honestly bracket the extrapolation
    uncertainty in the headline band; missing either makes the headline
    misleading. Set to False only for debugging.

    Returns a flat path dict containing the headline paths under their original
    keys (for backward compatibility with existing notebooks) plus prefixed
    ``headline__*`` and ``burke_sensitivity_{family}__*`` keys for each
    sub-run, and ``*_unc_dir`` entries for the output directories.
    """
    slug = (city or os.environ.get("CITY") or "rome").strip().lower()
    n_use = int(n if n is not None else os.environ.get("NB09_N", 512))
    seed_use = int(seed if seed is not None else os.environ.get("NB09_SEED", _base.SEED_DEFAULT))
    make_figures_env = os.environ.get("NB09_MAKE_FIGURES", "0").strip().lower() in {"1", "true", "yes", "y"}
    make_figures_use = make_figures_env if make_figures is None else bool(make_figures)
    burke_sens_env = os.environ.get("NB09_BURKE_SENSITIVITY", "1").strip().lower() in {"1", "true", "yes", "y"}
    burke_sens_use = burke_sens_env if burke_sensitivity is None else bool(burke_sensitivity)
    require_both_env = os.environ.get("NB09_REQUIRE_BOTH_MASSELOT", "1").strip().lower() in {"1", "true", "yes", "y"}
    require_both_use = require_both_env if require_both_masselot is None else bool(require_both_masselot)

    runner = NB09ImprovedFastMasselotMain(slug)

    # 1. Headline: Masselot-only LHS, headline output dir.
    runner.set_lhs_scope("masselot_headline", require_both_masselot=require_both_use)
    print(
        f"[NB09 masselot-main] Run type: masselot_headline_lhs_band — "
        f"Masselot extrapolation-bracketed LHS over "
        f"{runner.available_if_families}; output dir: {runner.unc_dir}"
    )
    headline_paths = runner.run(n=n_use, seed=seed_use, make_figures=make_figures_use)

    paths: dict[str, Path] = {"headline_unc_dir": runner.unc_dir}
    for key, p in headline_paths.items():
        paths[f"headline__{key}"] = p
        # Preserve backward-compatible flat keys (e.g. paths['samples']) for
        # any notebook code that already accesses the headline outputs by their
        # unprefixed name.
        paths.setdefault(key, p)

    # 2 & 3. Burke-conditional LHS sensitivity bands (optional, on by default).
    # Each is a full LHS run with IF_FAMILY pinned to one Burke family; all
    # other uncertainty dimensions are sampled normally. The outputs are bands,
    # not deterministic point comparisons vs the Masselot headline.
    if burke_sens_use:
        for burke_family in ("burke_polynomial", "burke_powerlaw"):
            if burke_family not in runner._full_available_if_families:
                print(
                    f"[NB09 masselot-main] Skipping Burke-conditional LHS band {burke_family!r}: "
                    f"not available on {slug!r}."
                )
                continue
            runner.set_lhs_scope("burke_sensitivity", family=burke_family)
            print(
                f"[NB09 masselot-main] Run type: burke_conditional_lhs_band — "
                f"full LHS with IF_FAMILY pinned to {burke_family!r}; "
                f"output dir: {runner.unc_dir}"
            )
            burke_paths = runner.run(n=n_use, seed=seed_use, make_figures=False)
            paths[f"burke_sensitivity_{burke_family}_unc_dir"] = runner.unc_dir
            for key, p in burke_paths.items():
                paths[f"burke_sensitivity_{burke_family}__{key}"] = p

    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Masselot-main improved-fast March2026 NB09 uncertainty workflow.")
    parser.add_argument("--city", default=os.environ.get("CITY", "rome"), help="City slug")
    parser.add_argument("--n", type=int, default=int(os.environ.get("NB09_N", 512)), help="Latin hypercube sample size")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("NB09_SEED", _base.SEED_DEFAULT)), help="Sampling seed")
    parser.add_argument("--figures-only", action="store_true", help="Regenerate saved figures from existing outputs")
    parser.add_argument("--make-figures", action="store_true", help="Generate figures during the uncertainty run")
    parser.add_argument(
        "--no-burke-sensitivity",
        action="store_true",
        help=(
            "Skip the two Burke IF-conditional LHS sensitivity runs "
            "(headline Masselot-only run only)."
        ),
    )
    parser.add_argument(
        "--no-require-both-masselot",
        action="store_true",
        help=(
            "Allow the headline run to proceed if only one Masselot family is "
            "available. Production runs should leave this off so both "
            "'masselot' and 'masselot_tail' are required."
        ),
    )
    args = parser.parse_args()
    if args.figures_only:
        out_dir = regenerate_saved_figures(args.city)
        print(f"Regenerated improved figures in: {out_dir}")
    else:
        paths = run_nb09_improved_fast(
            city=args.city,
            n=args.n,
            seed=args.seed,
            make_figures=args.make_figures,
            burke_sensitivity=not args.no_burke_sensitivity,
            require_both_masselot=not args.no_require_both_masselot,
        )
        print("Saved Masselot-main improved-fast uncertainty outputs in:")
        for key, path in paths.items():
            print(f"  {key}: {path}")


if __name__ == "__main__":
    main()
