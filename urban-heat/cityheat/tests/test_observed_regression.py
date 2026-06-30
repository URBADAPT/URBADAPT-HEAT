"""Regression: wiring the income-source switch must not change OBSERVED behaviour.

Portable (no /tmp backups). Asserts that for observed mode the resolver yields the
prior config-driven inputs, that the injected switch cells add no cross-city drift,
and that the emulator additions in NB05 are guarded by INCOME_SOURCE=='emulator'
(hence inert under observed).

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

_AGNOSTIC = os.path.join(_URBAN_HEAT, "notebooks", "city_agnostic", "March2026_agnostic")
_CITIES = ["Rome", "Athens", "Lisbon", "Copenhagen"]


def _nb05(city):
    return json.load(open(glob.glob(os.path.join(_AGNOSTIC, city, "05_*.ipynb"))[0]))


def test_observed_resolver_equals_prior_config():
    cfg = yaml.safe_load(open(os.path.join(_URBAN_HEAT, "configs", "rome.yml")))
    spec = resolve_income_inputs(cfg)
    assert spec["source"] == "observed"
    assert spec["csv"] == cfg["files"]["income_csv"]
    assert spec["columns"] == cfg["income"]["columns"]
    assert spec["aggregation"] == cfg["income"].get("aggregation", "mean")


def test_switch_cells_identical_across_cities():
    def income_cells(city):
        return [ "".join(c["source"]) for c in _nb05(city)["cells"]
                 if any("income" in t for t in c.get("metadata", {}).get("tags", [])) ]
    ref = income_cells("Rome")
    assert len(ref) == 2, f"expected resolver + override cells, got {len(ref)}"
    for city in _CITIES[1:]:
        assert income_cells(city) == ref, f"{city} switch cells differ -> added drift"


def test_emulator_additions_are_guarded():
    src = "\n".join("".join(c["source"]) for c in _nb05("Rome")["cells"])
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
