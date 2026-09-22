"""Regression: wiring the income-source switch must not change OBSERVED behaviour.

Portable (no /tmp backups). Asserts that for observed mode the resolver yields the
prior config-driven inputs, that the current NB05 template contains the income
switch cells, and that emulator additions are guarded by
INCOME_SOURCE=='emulator' (hence inert under observed).

    pytest urban-heat/cityheat/tests/test_observed_regression.py
    python  urban-heat/cityheat/tests/test_observed_regression.py
"""
import glob
import json
import os
import sys

import yaml

_HERE = os.path.dirname(__file__)
_URBAN_HEAT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _URBAN_HEAT)
from cityheat.income_source import resolve_income_inputs  # noqa: E402

_TEMPLATE = os.path.join(
    _URBAN_HEAT, "notebooks", "city_agnostic", "March2026_agnostic", "template"
)


def _nb05():
    matches = glob.glob(os.path.join(_TEMPLATE, "05_*.ipynb"))
    assert len(matches) == 1, f"expected one NB05 template, got {matches}"
    with open(matches[0], encoding="utf-8") as handle:
        return json.load(handle)


def test_observed_resolver_equals_prior_config():
    cfg = yaml.safe_load(open(os.path.join(_URBAN_HEAT, "configs", "rome.yml")))
    spec = resolve_income_inputs(cfg)
    assert spec["source"] == "observed"
    assert spec["csv"] == cfg["files"]["income_csv"]
    assert spec["columns"] == cfg["income"]["columns"]
    assert spec["aggregation"] == cfg["income"].get("aggregation", "mean")


def test_switch_cells_present_in_template():
    income_cells = [
        "".join(c["source"]) for c in _nb05()["cells"]
        if any("income" in t for t in c.get("metadata", {}).get("tags", []))
    ]
    assert len(income_cells) == 2, f"expected resolver + override cells, got {len(income_cells)}"
    assert "resolve_income_inputs(cfg)" in income_cells[0]
    assert "load_emulator_inc_agg" in income_cells[1]


def test_emulator_additions_are_guarded():
    src = "\n".join("".join(c["source"]) for c in _nb05()["cells"])
    # the cell-21 guard and the override are both gated on the emulator source
    assert 'if INCOME_SOURCE == "emulator":' in src and "income_names = None" in src
    assert "load_emulator_inc_agg" in src
    assert "resolve_income_inputs(cfg)" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"ALL {len(fns)} TESTS PASSED")
