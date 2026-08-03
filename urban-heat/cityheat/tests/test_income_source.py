"""Unit tests for cityheat.income_source.

Runnable two ways:
    pytest urban-heat/cityheat/tests/test_income_source.py
    /opt/anaconda3/envs/urbanheat/bin/python urban-heat/cityheat/tests/test_income_source.py

Covers the normalisers, the match_by routing, the recovered name-keyed join, and
the two regression tests for the bug this module fixes: (a) the coverage assert
must fire on a code-vs-name key-type mismatch, and (b) the original silent failure
(to_cap5 applied to names -> 0 zones) must now raise instead of filling city-mean.
"""
import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from cityheat.income_source import (  # noqa: E402
    norm_name, to_cap5, zone_key, resolve_income_inputs, load_emulator_inc_agg)


def _emu_csv(rows, d):
    p = os.path.join(d, "emu.csv")
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_normalisers():
    assert to_cap5("110") == "00110"
    assert pd.isna(to_cap5("Amager Øst"))            # a NAME -> NaN (the bug trigger)
    assert norm_name("Amager Øst") == "amager ost"
    assert norm_name("Sendling-Westpark") == "sendling westpark"


def test_zone_key_routes_by_match_by():
    assert list(zone_key(["00118", "9"], "code")) == ["00118", "00009"]
    assert list(zone_key(["Amager Øst"], "name")) == ["amager ost"]
    assert list(zone_key(["SEPOLIA"], "spatial_join")) == ["sepolia"]


def test_resolve_observed_vs_emulator():
    obs = resolve_income_inputs(
        {"files": {"income_csv": "x.csv"}, "income": {"aggregation": "weighted"}})
    assert obs["source"] == "observed" and obs["aggregation"] == "weighted" and obs["csv"] == "x.csv"
    emu = resolve_income_inputs(
        {"income": {"source": "emulator", "emulator": {"csv": "e.csv", "city": "Berlin"}}})
    assert emu["source"] == "emulator" and emu["aggregation"] == "mean" and emu["format"] == "tabular"


def test_emulator_name_match_recovers_the_join():
    with tempfile.TemporaryDirectory() as d:
        csv = _emu_csv([{"city": "Copenhagen", "subcity_code": n, "income_index_pred": v}
                        for n, v in [("Amager Øst", 1.2), ("Bispebjerg", 0.8), ("Vesterbro", 1.0)]], d)
        spec = resolve_income_inputs(
            {"income": {"source": "emulator", "emulator": {"csv": csv, "city": "Copenhagen"}}})
        inc = load_emulator_inc_agg(spec, "name", zone_codes=["amager ost", "bispebjerg", "vesterbro"])
        assert set(inc.zone_code) == {"amager ost", "bispebjerg", "vesterbro"}
        assert abs(float(inc.loc[inc.zone_code == "amager ost", "inc_mean"].iloc[0]) - 1.2) < 1e-9


def test_emulator_code_match():
    with tempfile.TemporaryDirectory() as d:
        csv = _emu_csv([{"city": "Roma", "subcity_code": c, "income_index_pred": v}
                        for c, v in [("00118", 0.4), ("00119", 0.3)]], d)
        spec = resolve_income_inputs(
            {"income": {"source": "emulator", "emulator": {"csv": csv, "city": "Roma"}}})
        inc = load_emulator_inc_agg(spec, "code", zone_codes=["00118", "00119"])
        assert set(inc.zone_code) == {"00118", "00119"}


def test_coverage_assert_fires_on_keytype_mismatch():
    # emulator keyed by CODE, zones matched by NAME (the Lisbon case) -> must FAIL loud
    with tempfile.TemporaryDirectory() as d:
        csv = _emu_csv([{"city": "Lisbon", "subcity_code": c, "income_index_pred": 1.0}
                        for c in ["110601", "110602"]], d)
        spec = resolve_income_inputs(
            {"income": {"source": "emulator", "emulator": {"csv": csv, "city": "Lisbon"}}})
        try:
            load_emulator_inc_agg(spec, "name", zone_codes=["alvalade", "benfica"], min_coverage=0.95)
        except ValueError:
            return
        raise AssertionError("coverage assert should have fired on code-vs-name mismatch")


def test_original_silent_bug_now_raises():
    # to_cap5 applied to district NAMES -> 0 zones; must raise, not fill city-mean
    with tempfile.TemporaryDirectory() as d:
        csv = _emu_csv([{"city": "Copenhagen", "subcity_code": "Amager Øst", "income_index_pred": 1.0}], d)
        spec = resolve_income_inputs(
            {"income": {"source": "emulator", "emulator": {"csv": csv, "city": "Copenhagen"}}})
        try:
            load_emulator_inc_agg(spec, "code", zone_codes=["amager ost"])
        except ValueError:
            return
        raise AssertionError("should raise when to_cap5 yields 0 zones (the original silent bug)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"ALL {len(fns)} TESTS PASSED")
