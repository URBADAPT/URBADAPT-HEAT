"""Create the March2026 Masselot-main workflow-variant notebooks.

By default this creates:

    notebooks/city_agnostic/March2026_masselot_main/<City>/
    URBAN_HEAT_OUTPUT_VARIANT=masselot_main
    IF_MAIN_FAMILY=masselot_tail

The generated NB04 body is intentionally family-aware: switching a copied tree
to IF_MAIN_FAMILY=masselot later will promote normal constant-tail Masselot
instead, while keeping Burke polynomial/power-law as sensitivities.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import nbformat
from nbformat import NotebookNode
from nbformat.v4 import new_code_cell, new_markdown_cell


CITIES = ["Rome", "Lisbon", "Athens", "Copenhagen"]
SOURCE_TREE = Path("notebooks/city_agnostic/March2026")
DEFAULT_TARGET_TREE = Path("notebooks/city_agnostic/March2026_masselot_main")
DEFAULT_OUTPUT_VARIANT = "masselot_main"
DEFAULT_IF_MAIN_FAMILY = "masselot_tail"

def repo_root() -> Path:
    start = Path(__file__).resolve()
    for cand in [start, *start.parents]:
        if (cand / "cityheat").is_dir() and (cand / "notebooks").is_dir():
            return cand
    raise RuntimeError("Could not locate urban-heat repo root.")


def env_cell_source(output_variant: str, if_main_family: str) -> str:
    return (
        "import os\n"
        f'os.environ["URBAN_HEAT_OUTPUT_VARIANT"] = "{output_variant}"\n'
        f'os.environ["IF_MAIN_FAMILY"] = "{if_main_family}"\n'
    )


def first_code_index(nb: NotebookNode) -> int:
    for idx, cell in enumerate(nb.cells):
        if cell.cell_type == "code":
            return idx
    return len(nb.cells)


def is_variant_env_cell(cell: NotebookNode) -> bool:
    src = str(cell.get("source", ""))
    tags = set(cell.get("metadata", {}).get("tags", []))
    return (
        "masselot-main-setup" in tags
        or ("URBAN_HEAT_OUTPUT_VARIANT" in src and "IF_MAIN_FAMILY" in src and "masselot" in src)
    )


def upsert_env_cell(nb: NotebookNode, output_variant: str, if_main_family: str) -> None:
    cell = new_code_cell(
        source=env_cell_source(output_variant, if_main_family),
        metadata={"tags": ["masselot-main-setup"]},
    )
    for idx, existing in enumerate(nb.cells):
        if existing.cell_type == "code" and is_variant_env_cell(existing):
            nb.cells[idx] = cell
            return
    nb.cells.insert(first_code_index(nb), cell)


def clear_existing_outputs(nb: NotebookNode) -> None:
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        cell["execution_count"] = None
        cell["outputs"] = []


def nb04_masselot_main_cells() -> list[NotebookNode]:
    """Replacement NB04 body for the Masselot-main workflow variant."""
    return [
        new_markdown_cell(
            "## Main IF family and sensitivity registry\n\n"
            "This variant uses the Masselot city- and age-specific impact functions as the "
            "deterministic downstream IF family. Burke polynomial and power-law curves are "
            "written only as sensitivity/generalizability artifacts."
        ),
        new_markdown_cell(
            "## Masselot-main deterministic impact functions\n\n"
            "The helper below loads the Masselot constant-tail and log-linear-tail IF JSONs "
            "built from the Zenodo/Masselot reconstruction workflow, promotes the configured "
            "`IF_MAIN_FAMILY` to the canonical downstream IF slot inside this variant output "
            "namespace, writes Masselot diagnostics, and keeps Burke curves as sensitivities."
        ),
        new_code_cell(
            "from cityheat.nb04_masselot_main import run_nb04_masselot_main\n\n"
            "nb04_outputs = run_nb04_masselot_main(\n"
            "    root=ROOT,\n"
            "    out_dir=OUT,\n"
            "    int_dir=INT,\n"
            "    slug=SLUG,\n"
            "    city=CITY,\n"
            "    hazard=H,\n"
            "    exposures_by_year=exposures_by_year,\n"
            ")\n"
            "nb04_outputs\n",
            metadata={"tags": ["masselot-main-nb04-run"]},
        ),
        new_markdown_cell(
            "## NB04 outputs\n\n"
            "The canonical downstream files in `INT` now describe the configured Masselot "
            "main family, with metadata recording the true source and extrapolation "
            "assumption. The Burke polynomial and power-law files remain available only as "
            "sensitivity families."
        ),
        new_code_cell(
            "import json\n\n"
            "with open(nb04_outputs[\"manifest\"], \"r\") as f:\n"
            "    if_family_manifest = json.load(f)\n\n"
            "if_family_manifest\n",
            metadata={"tags": ["masselot-main-nb04-manifest"]},
        ),
    ]


def replace_nb04_body_with_masselot_main(nb: NotebookNode) -> None:
    """Remove the Burke-main NB04 body from copied notebooks."""
    if nb.cells and nb.cells[0].cell_type == "markdown":
        nb.cells[0].source = (
            "# Notebook 4 - Masselot-main age-specific impact functions for heat mortality\n\n"
            "This copied March2026 variant keeps the same hazards, exposures, "
            "vulnerability inputs, and downstream modules, but uses Masselot as the "
            "deterministic age-differentiated IF source and writes Burke only as "
            "sensitivity/generalizability output."
        )
    cut_idx = None
    for idx, cell in enumerate(nb.cells):
        if "## Diagnostic check on the daily hazard" in str(cell.get("source", "")):
            cut_idx = min(idx + 2, len(nb.cells))
            break
    cut_markers = [
        "## Main IF family and sensitivity registry",
        "from cityheat.nb04_masselot_main import run_nb04_masselot_main",
        "## Common temperature axis and reference temperature",
        "## From Burke-style deaths per 100k",
        "# Approximate calibration anchor points",
        "# Masselot-source main variant export",
    ]
    if cut_idx is None:
        for idx, cell in enumerate(nb.cells):
            src = str(cell.get("source", ""))
            if any(marker in src for marker in cut_markers):
                cut_idx = idx
                break
    if cut_idx is None:
        raise RuntimeError("Could not locate the NB04 Burke-main body to replace.")
    nb.cells = nb.cells[:cut_idx] + nb04_masselot_main_cells()


def patch_nb09_runtime_cache(nb: NotebookNode) -> None:
    old = (
        "os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'outputs' / '.mpl'))\n"
        "os.environ.setdefault('XDG_CACHE_HOME', str(ROOT / 'outputs' / '.cache'))\n"
        "Path(os.environ['MPLCONFIGDIR']).mkdir(parents=True, exist_ok=True)\n"
        "Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True, exist_ok=True)\n"
    )
    old_variant_fallback = (
        "output_variant = os.environ.get('URBAN_HEAT_OUTPUT_VARIANT', '').strip()\n"
        "OUTPUT_ROOT = ROOT / 'outputs_variants' / output_variant if output_variant else ROOT / 'outputs'\n"
        "os.environ.setdefault('MPLCONFIGDIR', str(OUTPUT_ROOT / '.mpl'))\n"
        "os.environ.setdefault('XDG_CACHE_HOME', str(OUTPUT_ROOT / '.cache'))\n"
        "Path(os.environ['MPLCONFIGDIR']).mkdir(parents=True, exist_ok=True)\n"
        "Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True, exist_ok=True)\n"
    )
    new = (
        "output_variant = os.environ.get('URBAN_HEAT_OUTPUT_VARIANT', '').strip()\n"
        "if not output_variant:\n"
        "    raise RuntimeError('Masselot-main NB09 requires URBAN_HEAT_OUTPUT_VARIANT.')\n"
        "OUTPUT_ROOT = ROOT / 'outputs_variants' / output_variant\n"
        "os.environ.setdefault('MPLCONFIGDIR', str(OUTPUT_ROOT / '.mpl'))\n"
        "os.environ.setdefault('XDG_CACHE_HOME', str(OUTPUT_ROOT / '.cache'))\n"
        "Path(os.environ['MPLCONFIGDIR']).mkdir(parents=True, exist_ok=True)\n"
        "Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True, exist_ok=True)\n"
    )
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        src = str(cell.source)
        if old in src:
            cell.source = src.replace(old, new)
        elif old_variant_fallback in src:
            cell.source = src.replace(old_variant_fallback, new)


def patch_nb10_output_paths(nb: NotebookNode) -> None:
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        src = str(cell.source)
        if 'ROOT / "outputs" / CITY_SLUG' not in src:
            continue
        src = src.replace('ROOT / "outputs" / CITY_SLUG', "CITY_OUT")
        if "resolve_city_output" not in src:
            src = (
                "from cityheat.nbsetup_masselot_main import resolve_city_output\n"
                "CITY_OUT = resolve_city_output(ROOT, CITY_SLUG)\n\n"
                + src
            )
        cell.source = src


def patch_variant_imports(nb: NotebookNode) -> None:
    replacements = {
        "from cityheat.nbsetup import bootstrap": (
            "from cityheat.nbsetup_masselot_main import bootstrap"
        ),
        "from cityheat.nbsetup import resolve_city_output": (
            "from cityheat.nbsetup_masselot_main import resolve_city_output"
        ),
        "from cityheat.nb09_improved_fast import run_nb09_improved_fast": (
            "from cityheat.nb09_improved_fast_masselot_main import run_nb09_improved_fast"
        ),
        "from cityheat.nb09_improved_fast import regenerate_saved_figures": (
            "from cityheat.nb09_improved_fast_masselot_main import regenerate_saved_figures"
        ),
        "from cityheat.nb10_summary import run_nb10_summary": (
            "from cityheat.nb10_summary_masselot_main import run_nb10_summary"
        ),
    }
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        src = str(cell.source)
        for old, new in replacements.items():
            src = src.replace(old, new)
        cell.source = src


def copy_city_notebooks(root: Path, city: str, target_tree: Path) -> list[Path]:
    src_dir = root / SOURCE_TREE / city
    dst_dir = root / target_tree / city
    if not src_dir.is_dir():
        raise FileNotFoundError(src_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for src in sorted(src_dir.glob("*.ipynb")):
        if src.name.endswith(".out.ipynb"):
            continue
        dst = dst_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def patch_notebook(path: Path, output_variant: str, if_main_family: str) -> None:
    nb = nbformat.read(path, as_version=4)
    clear_existing_outputs(nb)
    upsert_env_cell(nb, output_variant, if_main_family)
    patch_variant_imports(nb)
    if path.name.startswith("04_impact_functions"):
        replace_nb04_body_with_masselot_main(nb)
    if "09_uncertainty" in path.name and "improved_fast" in path.name:
        patch_nb09_runtime_cache(nb)
    if path.name.startswith("10_summary"):
        patch_nb10_output_paths(nb)
    nbformat.write(nb, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-tree", type=Path, default=DEFAULT_TARGET_TREE)
    parser.add_argument("--output-variant", default=DEFAULT_OUTPUT_VARIANT)
    parser.add_argument("--if-main-family", default=DEFAULT_IF_MAIN_FAMILY, choices=["masselot", "masselot_tail"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = repo_root()
    all_paths: list[Path] = []
    for city in CITIES:
        all_paths.extend(copy_city_notebooks(root, city, args.target_tree))
    for path in all_paths:
        patch_notebook(path, args.output_variant, args.if_main_family)
    print(
        f"Copied and patched {len(all_paths)} notebooks under {root / args.target_tree} "
        f"({args.output_variant} / {args.if_main_family})"
    )


if __name__ == "__main__":
    main()
