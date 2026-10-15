"""The published results use both hazard-curve corrections; a plain run must too."""
from __future__ import annotations

import json
import os
from pathlib import Path

from cityheat import nbsetup_masselot_main as setup

TEMPLATES = Path(__file__).resolve().parents[2] / "notebooks" / "city_agnostic" / "March2026_agnostic" / "template"


def _code(nb_path: Path) -> str:
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    return "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")


def test_default_impact_family_is_variance_corrected():
    previous = os.environ.pop("IF_MAIN_FAMILY", None)
    try:
        assert setup.get_if_main_family() == "masselot_tail_varcorr"
        assert setup.masselot_extrapolation() == "loglinear_tail"
    finally:
        if previous is not None:
            os.environ["IF_MAIN_FAMILY"] = previous


def test_notebooks_default_to_both_corrections():
    notebooks = sorted(TEMPLATES.glob("*.ipynb"))
    assert notebooks, TEMPLATES
    for nb in notebooks:
        code = _code(nb)
        # A forced family would override the caller and the canonical default alike.
        assert 'os.environ["IF_MAIN_FAMILY"] =' not in code, nb.name
    nb02 = _code(TEMPLATES / "02_grids_0126.ipynb")
    assert 'clim_cfg.get("t2m_era5_bias_correct", True)' in nb02


if __name__ == "__main__":
    test_default_impact_family_is_variance_corrected()
    test_notebooks_default_to_both_corrections()
    print("canonical defaults OK")
