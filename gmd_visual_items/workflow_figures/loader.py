"""Locate and load persisted URBADAPT outputs for a city -- decoupled.

Every artifact the figures need is read straight off disk from
``outputs_variants/<variant>/<city>/`` (``tables/``, ``interim/``, ...). This
module deliberately does NOT import ``cityheat`` (which pulls in ``climada``),
so figure generation needs only numpy / pandas.

The pipeline writes a stable, snapshot set of files; see the field-name map in
``figures.py`` for what each figure consumes.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

DEFAULT_VARIANT = 'masselot_main'


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the ``urban-heat`` package dir (holds ``cityheat`` + ``outputs_variants``).

    Robust to where this package physically lives: it works whether the code
    sits inside ``urban-heat/`` or elsewhere in the repo (e.g. under
    ``gmd_visual_items/``). For each ancestor of the caller we accept either
    the ancestor itself or its ``urban-heat/`` child as the output root.
    """
    start = start or Path(__file__).resolve()

    def _is_root(p: Path) -> bool:
        return (p / 'cityheat').is_dir() and (p / 'outputs_variants').is_dir()

    for cand in [start, *start.parents]:
        if _is_root(cand):
            return cand
        if _is_root(cand / 'urban-heat'):
            return cand / 'urban-heat'
    raise FileNotFoundError(
        "Could not locate the urban-heat output root (expected a dir with both "
        "'cityheat/' and 'outputs_variants/', either directly or under 'urban-heat/')."
    )


@dataclass
class CityOutputs:
    """Handle to one city's persisted output tree."""
    slug: str
    city_name: str
    out: Path

    @property
    def interim(self) -> Path:
        return self.out / 'interim'

    @property
    def tables(self) -> Path:
        return self.out / 'tables'

    @property
    def figures(self) -> Path:
        return self.out / 'figures'

    def first(self, *rel_candidates: str) -> Path | None:
        """Return the first existing path among ``out``-relative candidates."""
        for rel in rel_candidates:
            p = self.out / rel
            if p.exists():
                return p
        return None

    def latest_glob(self, subdir: str, pattern: str) -> Path | None:
        matches = sorted((self.out / subdir).glob(pattern))
        if not matches:
            return None
        return max(matches, key=lambda p: p.stat().st_mtime)

    def require(self, label: str, *rel_candidates: str) -> Path:
        p = self.first(*rel_candidates)
        if p is None:
            joined = '\n  '.join(str(self.out / r) for r in rel_candidates)
            raise FileNotFoundError(f'[{label}] none of these exist:\n  {joined}')
        return p

    @staticmethod
    def npz(path: Path) -> dict[str, np.ndarray]:
        with np.load(path) as data:
            return {k: data[k] for k in data.files}

    @staticmethod
    def json(path: Path) -> dict:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)

    @staticmethod
    def csv(path: Path) -> pd.DataFrame:
        return pd.read_csv(path)

    def city_mask(self) -> np.ndarray:
        path = self.require('city mask', 'interim/city_mask.npz')
        return self.npz(path)['city_mask'].astype(bool)


def load_city(city: str, *, variant: str = DEFAULT_VARIANT, root: Path | None = None) -> CityOutputs:
    """Build a :class:`CityOutputs` for ``city`` under the given output variant.

    ``city`` may be the slug (``"rome"``); ``city_name`` is taken from
    ``configs/<slug>.yml`` when present, else title-cased.
    """
    root = root or find_repo_root()
    slug = city.strip().lower()
    out = root / 'outputs_variants' / variant / slug
    if not out.is_dir():
        raise FileNotFoundError(
            f"No output tree for '{slug}' under variant '{variant}':\n  {out}\n"
            f"Has the pipeline been run for this city?"
        )
    city_name = slug.title()
    cfg_path = root / 'configs' / f'{slug}.yml'
    if cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
            city_name = str(cfg.get('city_name', city_name))
        except Exception:
            pass
    return CityOutputs(slug=slug, city_name=city_name, out=out)
