from __future__ import annotations

import argparse
import re
from pathlib import Path

import nbformat
from nbformat import NotebookNode
from nbformat.v4 import new_code_cell

CITIES = ["Rome", "Athens", "Lisbon", "Copenhagen"]
SOURCE_TREE = Path("notebooks/city_agnostic/March2026_masselot_main")
TARGET_TREE = Path("notebooks/city_agnostic/March2026_agnostic")
OUTPUT_VARIANT = "masselot_main_agnostic"
IF_MAIN_FAMILY = "masselot_tail"
NB_NUMS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]

# Per-notebook base city. Copenhagen is the most-evolved superset for the
# de-drift-heavy notebooks; Rome (warm consensus) for the trivial ones.
BASE_BY_NB = {
    "01": "Rome", "02": "Rome", "09": "Rome",
    "03": "Copenhagen", "04": "Copenhagen", "05": "Copenhagen",
    "06": "Copenhagen", "07": "Copenhagen", "08": "Copenhagen", "10": "Copenhagen",
}

ENV_TAG = "agnostic-setup"
SEL_TAG = "agnostic-city-selector"


def repo_root() -> Path:
    start = Path(__file__).resolve()
    for cand in [start, *start.parents]:
        if (cand / "cityheat").is_dir() and (cand / "notebooks").is_dir():
            return cand
    raise RuntimeError("Could not locate urban-heat repo root.")


def nb_path(root: Path, tree: Path, city: str, num: str) -> Path:
    cands = [p for p in sorted((root / tree / city).glob(f"{num}_*.ipynb"))
             if not p.name.endswith(".out.ipynb")]
    if len(cands) != 1:
        raise RuntimeError(f"Expected exactly 1 notebook for {city}/{num}, got {cands}")
    return cands[0]

# Cell helpers
def env_cell_source() -> str:
    return (
        "import os\n"
        f'os.environ["URBAN_HEAT_OUTPUT_VARIANT"] = "{OUTPUT_VARIANT}"\n'
        f'os.environ["IF_MAIN_FAMILY"] = "{IF_MAIN_FAMILY}"\n'
    )


def selector_cell_source(city_name: str, setdefault: bool = False) -> str:
    op = (f'os.environ.setdefault("CITY", "{city_name}")'
          if setdefault else f'os.environ["CITY"] = "{city_name}"')
    return (
        "# City selector - the ONLY per-city line in this agnostic notebook.\n"
        "# Set CITY to rome / athens / lisbon / copenhagen (any configured city).\n"
        "import os\n"
        f"{op}\n"
    )


def clear_outputs(nb: NotebookNode) -> None:
    for c in nb.cells:
        if c.cell_type == "code":
            c["execution_count"] = None
            c["outputs"] = []


def first_code_index(nb: NotebookNode) -> int:
    for i, c in enumerate(nb.cells):
        if c.cell_type == "code":
            return i
    return len(nb.cells)


def is_env_cell(c: NotebookNode) -> bool:
    s = str(c.get("source", ""))
    tags = set(c.get("metadata", {}).get("tags", []))
    return ENV_TAG in tags or ("URBAN_HEAT_OUTPUT_VARIANT" in s and "IF_MAIN_FAMILY" in s)


def is_selector_cell(c: NotebookNode) -> bool:
    s = str(c.get("source", ""))
    tags = set(c.get("metadata", {}).get("tags", []))
    return SEL_TAG in tags or (
        'os.environ["CITY"]' in s
        and ("selector" in s.lower() or "pick the city" in s.lower())
    )


def upsert_cell(nb: NotebookNode, predicate, source: str, tag: str, fallback_index: int) -> int:
    cell = new_code_cell(source=source, metadata={"tags": [tag]})
    for i, c in enumerate(nb.cells):
        if c.cell_type == "code" and predicate(c):
            nb.cells[i] = cell
            return i
    nb.cells.insert(fallback_index, cell)
    return fallback_index


# Genericisation (surgical, safe). Order matters: neutralise the SLUG fallback
# (which contains a quoted city name) BEFORE blanket capital-city replacement.
# Only CAPITAL city names (comments / display strings) are touched; the lowercase
# slug logic ("copenhagen") and runtime f"{CITY}" titles are left intact.
_SLUG_DEFAULT = re.compile(r'os\.environ\.get\(\s*["\']CITY["\']\s*,\s*["\'][A-Za-z]+["\']\s*\)')
_INCOME_COMMENT = re.compile(
    r"# Load the zone layer tied to the income dataset used for AC downscaling \([^)]*\)\.")
_CAPITAL_CITY = re.compile(r"\b(Rome|Roma|Athens|Athina|Lisbon|Lisboa|Copenhagen|K[oø]benhavn|Kobenhavn)\b")


def genericise(nb: NotebookNode) -> None:
    for c in nb.cells:
        if c.cell_type != "code":
            continue
        s = str(c.source)
        s = _SLUG_DEFAULT.sub('os.environ["CITY"]', s)
        s = _INCOME_COMMENT.sub(
            "# Load the zone layer tied to the income dataset used for AC downscaling "
            "(config-driven zones: shapefile|OSM; income: tabular|raster).", s)
        s = _CAPITAL_CITY.sub("the city", s)
        c.source = s

# NB03: write BOTH city-mean conventions (base = Copenhagen, whose save cell
# currently uses the fraction-weighted mean as the primary column).
NB03_SAVE_MARKER = "Close any previously open HDF5 handles"
NB03_OLD_COMMENT = "# Use fraction-weighted means so values stay consistent with the city mask."
NB03_DUAL_COMMENT = (
    "# --- Two diagnostic city-mean conventions (neither feeds any downstream result;\n"
    "#     deaths use per-cell H.intensity and NB05 waste-heat recomputes its own mask mean) ---\n"
    "#  * mean_intensity_degC          = plain mean over ALL grid cells incl. zero off-mask cells\n"
    "#                                   (diluted by tile size); the long-standing convention.\n"
    "#  * mean_intensity_citymask_degC = fraction-weighted sum(I*frac)/sum(frac) = true mean over\n"
    "#                                   city-mask cells only. Equal to the plain mean iff fraction==1."
)
NB03_PLAIN_LINE = (
    "mean_by_event = np.asarray(H.intensity.mean(axis=1)).ravel()  "
    "# plain mean (primary, long-standing convention)\n")
NB03_DAILY_ANCHOR = '"mean_intensity_degC": mean_by_event,'
NB03_YEARLY_ANCHOR = 'citymean_degC=("mean_intensity_degC", "mean")'


def apply_nb03_dual(nb: NotebookNode) -> None:
    for c in nb.cells:
        if c.cell_type != "code" or NB03_SAVE_MARKER not in str(c.source):
            continue
        s = str(c.source)
        if "mean_intensity_citymask_degC" in s:
            return
        if s.count("mean_by_event = np.divide(") != 1:
            raise RuntimeError("NB03: expected exactly one 'mean_by_event = np.divide(' (base changed).")
        s = s.replace("mean_by_event = np.divide(", "mean_by_event_citymask = np.divide(", 1)
        for anchor, repl in [
            (NB03_OLD_COMMENT, NB03_DUAL_COMMENT),
            ("daily_summary = pd.DataFrame(", NB03_PLAIN_LINE + "daily_summary = pd.DataFrame("),
            (NB03_DAILY_ANCHOR, NB03_DAILY_ANCHOR + '\n    "mean_intensity_citymask_degC": mean_by_event_citymask,'),
            (NB03_YEARLY_ANCHOR, NB03_YEARLY_ANCHOR + ',\n        citymean_citymask_degC=("mean_intensity_citymask_degC", "mean")'),
        ]:
            if anchor not in s:
                raise RuntimeError(f"NB03 dual: anchor not found: {anchor!r}")
            s = s.replace(anchor, repl, 1)
        c.source = s
        return
    raise RuntimeError("NB03 save cell not found for dual city-mean transform.")

# Template build + city stamp
def build_template(root: Path, num: str) -> NotebookNode:
    base_city = BASE_BY_NB[num]
    nb = nbformat.read(nb_path(root, SOURCE_TREE, base_city, num), as_version=4)
    clear_outputs(nb)
    genericise(nb)
    env_i = upsert_cell(nb, is_env_cell, env_cell_source(), ENV_TAG, first_code_index(nb))
    upsert_cell(nb, is_selector_cell, selector_cell_source("Rome", setdefault=True),
                SEL_TAG, env_i + 1)
    if num == "03":
        apply_nb03_dual(nb)
    return nb


def stamp_city(template_file: Path, city_name: str) -> NotebookNode:
    nb = nbformat.read(template_file, as_version=4)
    for c in nb.cells:
        if c.cell_type == "code" and is_selector_cell(c):
            c.source = selector_cell_source(city_name, setdefault=False)
            c["metadata"]["tags"] = [SEL_TAG]
            return nb
    raise RuntimeError(f"No selector cell found to stamp for {city_name}")


def template_filename(base_name: str) -> str:
    return re.sub(r"_(Rome|Athens|Lisbon|Copenhagen)", "", base_name)


def city_filename(base_name: str, city: str) -> str:
    return re.sub(r"_(Rome|Athens|Lisbon|Copenhagen)", f"_{city}", base_name)


def self_check(root: Path) -> None:
    problems = []
    for num in NB_NUMS:
        per_city = {}
        for city in CITIES:
            nb = nbformat.read(nb_path(root, TARGET_TREE, city, num), as_version=4)
            sig = []
            for c in nb.cells:
                src = str(c.source)
                if c.cell_type == "code" and is_selector_cell(c):
                    src = "<<CITY-SELECTOR>>"
                sig.append((c.cell_type, src))
            per_city[city] = sig
        ref = per_city[CITIES[0]]
        for city in CITIES[1:]:
            if per_city[city] != ref:
                for i, (a, b) in enumerate(zip(ref, per_city[city])):
                    if a != b:
                        problems.append(f"NB{num}: {CITIES[0]} vs {city} differ at cell {i}")
                        break
                else:
                    problems.append(f"NB{num}: {CITIES[0]} vs {city} differ in length")
    if problems:
        raise SystemExit("STRUCTURAL SELF-CHECK FAILED:\n  " + "\n  ".join(problems))
    print(f"[self-check] OK: all 4 city notebooks identical except the selector cell "
          f"({len(NB_NUMS)} notebooks).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    nums = args.only or NB_NUMS
    root = repo_root()
    (root / TARGET_TREE / "template").mkdir(parents=True, exist_ok=True)
    for city in CITIES:
        (root / TARGET_TREE / city).mkdir(parents=True, exist_ok=True)
    for num in nums:
        base_name = nb_path(root, SOURCE_TREE, BASE_BY_NB[num], num).name
        tmpl = build_template(root, num)
        tmpl_file = root / TARGET_TREE / "template" / template_filename(base_name)
        nbformat.write(tmpl, tmpl_file)
        for city in CITIES:
            nbformat.write(stamp_city(tmpl_file, city),
                           root / TARGET_TREE / city / city_filename(base_name, city))
    print(f"Built {len(nums)} agnostic notebook(s) -> {root / TARGET_TREE} (variant={OUTPUT_VARIANT})")
    self_check(root)


if __name__ == "__main__":
    main()
