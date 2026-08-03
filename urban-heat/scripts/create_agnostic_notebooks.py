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

# Per-notebook base city. 
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


# Genericisation. Order matters: neutralise the SLUG fallback
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

# NB05: explicit, config-driven income-source switch (observed | emulator).
# `observed` reproduces prior behaviour byte-for-byte (EFF_* == the original
# expressions); `emulator` redirects the income load to the within-city income
# emulator's per-zone prediction CSV (income_index_pred fed as the per-zone
# income; the downstream percentile-rank + AC sigmoid are UNCHANGED). Proven
# order-exact: weighted_percentile_rank(income_index_pred, pop) reproduces the
# emulator's emitted p_inc (Spearman 1.0).
NB05_INCOME_ANCHOR = 'income_csv = P(files["income_csv"])'
NB05_SWITCH_MARKER = "Income source switch (config-driven"
NB05_SWITCH_TAG = "agnostic-income-switch"
NB05_OVERRIDE_TAG = "agnostic-income-emulator-override"
NB05_REPLACEMENTS = [
    ('inc_format  = inc_cfg.get("format", "tabular")  # "tabular" or "raster"',
     'inc_format  = EFF_INCOME_FORMAT  # "tabular" or "raster" (emulator forces tabular; see income-source switch)'),
    ('income_csv = P(files["income_csv"])',
     'income_csv = (Path(EFF_INCOME_CSV) if Path(str(EFF_INCOME_CSV)).is_absolute() else P(EFF_INCOME_CSV))'),
    ('inc_cols = inc_cfg.get("columns", {})',
     'inc_cols = EFF_INCOME_COLS'),
    ('aggregation  = inc_cfg.get("aggregation", "mean")',
     'aggregation  = EFF_INCOME_AGG'),
    ('city_aliases = [c.upper() for c in inc_cfg.get("city_aliases", [CITY])]',
     'city_aliases = [str(c).upper() for c in EFF_CITY_ALIASES]'),
    # Emulator: don't let the (observed-style) income load filter the OSM zones by income
    # keys -- inc_agg is rebuilt from the emulator after the zones load (override cell below).
    ('# Load zone boundaries',
     'if INCOME_SOURCE == "emulator":\n'
     '    income_names = None  # emulator: zones not filtered by income keys; inc_agg rebuilt after load\n\n'
     '# Load zone boundaries'),
]


def nb05_switch_cell_source() -> str:
    return (
        "# === Income source switch (config-driven, explicit): observed | emulator ===\n"
        "# observed : load the city's measured sub-city income table (default; reproduces prior results).\n"
        "# emulator : the per-zone income is the within-city income emulator's prediction; inc_agg is\n"
        "#            (re)built below by cityheat.income_source with the correct join key + coverage assert.\n"
        "from cityheat.income_source import resolve_income_inputs\n"
        "INCOME_SPEC   = resolve_income_inputs(cfg)\n"
        "INCOME_SOURCE = INCOME_SPEC['source']\n"
        "EFF_INCOME_CSV    = INCOME_SPEC['csv']\n"
        "EFF_INCOME_COLS   = INCOME_SPEC['columns']\n"
        "EFF_INCOME_AGG    = INCOME_SPEC['aggregation']\n"
        "EFF_INCOME_FORMAT = INCOME_SPEC['format']\n"
        "EFF_CITY_ALIASES  = INCOME_SPEC['city_aliases'] or [CITY]\n"
        "print(f'[income] source={INCOME_SOURCE}  ->  {EFF_INCOME_CSV}')\n"
        "INCOME_SOURCE_NOTE = INCOME_SOURCE  # provenance, recorded alongside AC outputs\n"
    )


def nb05_override_cell_source() -> str:
    return (
        "# === income.source: rebuild inc_agg (emulator) + record provenance (both modes) ===\n"
        "# emulator -> build inc_agg from the prediction, keyed to the AC zones via cityheat\n"
        "# (correct match_by join + loud coverage assert). observed -> keep the cell-above's inc_agg.\n"
        "if INCOME_SOURCE == 'emulator':\n"
        "    from cityheat.income_source import load_emulator_inc_agg\n"
        "    _min_cov = float((cfg.get('income', {}).get('emulator', {}) or {}).get('min_coverage', 0.95))\n"
        "    inc_agg = load_emulator_inc_agg(INCOME_SPEC, zone_match,\n"
        "                                    zone_codes=zone_ref['zone_code'], min_coverage=_min_cov)\n"
        "# Provenance: record the income source + zone match-rate alongside the AC outputs.\n"
        "import json as _json\n"
        "_zk = set(zone_ref['zone_code']); _ik = set(inc_agg['zone_code'])\n"
        "_prov = {'income_source': INCOME_SOURCE, 'n_income_zones': int(len(inc_agg)),\n"
        "         'n_ac_zones': int(len(_zk)), 'n_matched': int(len(_zk & _ik)),\n"
        "         'match_rate': round(len(_zk & _ik) / max(1, len(_zk)), 4),\n"
        "         'emulator_csv': (INCOME_SPEC.get('csv') if INCOME_SOURCE == 'emulator' else None)}\n"
        "(INT / f'income_provenance_{SLUG}.json').write_text(_json.dumps(_prov, indent=2))\n"
        "print('[income provenance]', _prov)\n"
    )


def apply_nb05_income_switch(nb: NotebookNode) -> None:
    """Insert the income-source resolver cell and rewire the income-load cell.

    Idempotent and order-safe: asserts each anchor is present exactly once (so it
    fails loud if the base notebook drifts), and is a no-op if already applied.
    """
    for c in nb.cells:
        if c.cell_type == "code" and NB05_SWITCH_MARKER in str(c.source):
            return  # already applied
    for i, c in enumerate(nb.cells):
        if c.cell_type != "code" or NB05_INCOME_ANCHOR not in str(c.source):
            continue
        s = str(c.source)
        for a, b in NB05_REPLACEMENTS:
            if s.count(a) != 1:
                raise RuntimeError(f"NB05 switch: expected exactly one {a!r}, found {s.count(a)} (base changed).")
            s = s.replace(a, b, 1)
        c.source = s
        # override cell AFTER the income/zone cell, resolver cell BEFORE it.
        nb.cells.insert(i + 1, new_code_cell(source=nb05_override_cell_source(),
                                             metadata={"tags": [NB05_OVERRIDE_TAG]}))
        nb.cells.insert(i, new_code_cell(source=nb05_switch_cell_source(),
                                         metadata={"tags": [NB05_SWITCH_TAG]}))
        return
    raise RuntimeError("NB05 income anchor not found (base changed).")

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
    if num == "05":
        apply_nb05_income_switch(nb)
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
