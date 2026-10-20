"""AC electricity and waste-heat warming (cityheat/ac_heat_energy.py) and their use in NB09."""
import numpy as np
import pytest

from cityheat import ac_heat_energy as ahe
from cityheat import nb09_improved_fast as nb09


def test_defaults_and_city_overrides():
    p = ahe.params({})
    assert p["method"] == "flux"
    assert p["waste_heat"]["sensitivity_k_per_wm2"] == 0.02
    assert p["electricity"]["kwh_per_household_day_full"] == 15.0
    cfg = {"ac": {"waste_heat": {"method": "LUT", "activation": {"t_on_c": 20.0}},
                  "heat_energy": {"electricity": {"persons_per_household": 2.0}}}}
    q = ahe.params(cfg)
    assert q["method"] == "lut"
    assert q["activation"]["t_on_c"] == 20.0 and q["activation"]["t_full_c"] == 25.0
    assert q["electricity"]["persons_per_household"] == 2.0
    assert q["electricity"]["kwh_per_household_day_onset"] == 2.5
    assert ahe.DEFAULTS["electricity"]["persons_per_household"] == 2.3  # defaults untouched


def test_consumption_rule():
    p = ahe.params({})
    t = np.array([np.nan, 15.0, 18.0, 18.7, 21.5, 25.0, 30.0])
    a = ahe.activity(t, p)
    np.testing.assert_allclose(a, [0.0, 0.0, 0.0, 0.1, 0.5, 1.0, 1.0])
    e = ahe.household_kwh(a, p)
    np.testing.assert_allclose(e, [0.0, 0.0, 0.0, 2.5 + 1.25, 2.5 + 6.25, 15.0, 15.0])
    np.testing.assert_allclose(ahe.consumption_weight(t, p), e / 15.0)
    assert ahe.annual_kwh_per_user(t, p) == pytest.approx(e.sum() / 2.3)
    assert ahe.annual_kwh_per_user(t, p, e_full=20.0) > ahe.annual_kwh_per_user(t, p)


def test_flux_warming_formula():
    p = ahe.params({})
    users, area = 230_000.0, 1.0e9
    # 100,000 households x 15 kWh x 4.5 over 24 h = 281.25 MW over 1,000 km2 = 0.28125 W/m2
    expected = 0.02 * 0.28125
    assert ahe.warming_full_activity(users, area, p) == pytest.approx(expected)
    assert ahe.warming_full_activity(users, area, p, sensitivity=0.01, e_full=7.5) == pytest.approx(expected / 4)
    with pytest.raises(ValueError):
        ahe.warming_full_activity(users, 0.0, p)


def test_nb09_waste_heat_follows_the_users_and_member_parameters():
    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.ahe = ahe.params({})
    runner.wh_method = "flux"
    runner.wh_built_area_m2 = 2.0e9
    runner.ac_ssp_base = 2
    runner._coverage_series_cache = {}
    runner.coverage_years = np.array([2020, 2030])
    cov = np.array([[0.4, 0.6]])
    runner.coverage_full_cube = {"base": np.stack([cov, cov]), "policy": np.stack([cov + 0.1, cov + 0.1])}
    pop = np.array([[1_000_000.0, 1_000_000.0]])
    runner.population_grid_like_nb05 = lambda year, exp_ssp_idx: pop
    sample = {"ac_ssp": 2, "EXP_SSP_IDX": 0, "WH_SENS": 0.03, "AC_KWH_FULL": 12.0, "cop_enabled": False}
    d_t = runner.waste_heat_delta_for_years(np.array([2020, 2025]), sample, mode="base")
    users = 0.5 * 2_000_000.0
    np.testing.assert_allclose(d_t, ahe.warming_full_activity(users, 2.0e9, runner.ahe, 0.03, 12.0))
    d_pol = runner.waste_heat_delta_for_years(np.array([2020]), sample, mode="policy")
    assert d_pol[0] > d_t[0]


def test_nb09_member_electricity_scales_nb05_pattern():
    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.kwh_city_nb05 = {2020: 300.0, 2030: 400.0}
    runner._member_kwh_city = None
    assert runner.kwh_per_user_for_member(2025) == pytest.approx(350.0)
    assert runner.kwh_scale_for_member(2025) == 1.0
    runner._member_kwh_city = {2020: 300.0, 2030: 400.0}
    assert runner.kwh_scale_for_member(2025) == pytest.approx(1.0)
    runner._member_kwh_city = {2020: 600.0, 2030: 800.0}
    assert runner.kwh_scale_for_member(2027) == pytest.approx(2.0)


def test_zone_without_electricity_row_gets_the_city_mean():
    import pandas as pd

    # zone 0 has AC users but no NB05 electricity row; in 2030 nobody has AC yet
    cov = pd.DataFrame({"year": [2020] * 3 + [2030] * 3, "muni_id": [0, 1, 2] * 2,
                        "users": [50.0, 100.0, 300.0, 0.0, 0.0, 0.0]})
    kwh = pd.DataFrame({"year": [2020, 2020, 2030, 2030], "muni_id": [1, 2, 1, 2],
                        "kwh": [200.0, 400.0, 100.0, 300.0]})
    out = ahe.fill_unlisted_zone_kwh(cov, kwh, users="users", kwh_col="kwh")
    zone0 = out[out["muni_id"] == 0].set_index("year")["kwh"]
    assert zone0[2020] == pytest.approx((100 * 200 + 300 * 400) / 400)  # weighted by current AC users
    assert zone0[2030] == pytest.approx(200.0)  # no users anywhere: plain mean
    assert len(out) == len(kwh) + 2


def test_every_zone_listed_leaves_electricity_untouched():
    import pandas as pd

    cov = pd.DataFrame({"year": [2020, 2020], "muni_id": [1, 2], "users": [1.0, 2.0]})
    kwh = pd.DataFrame({"year": [2020, 2020], "muni_id": [1, 2], "kwh": [200.0, 400.0]})
    assert ahe.fill_unlisted_zone_kwh(cov, kwh, users="users", kwh_col="kwh") is kwh
