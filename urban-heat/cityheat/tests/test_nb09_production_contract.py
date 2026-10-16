from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from cityheat import nb09_improved_fast as nb09


def _synthetic_runner_and_result():
    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.slug = "test-city"
    runner.coverage_for_sample = lambda year, ac_ssp, mode="base": np.full(4, 0.30 if mode == "policy" else 0.20)

    n = nb09.HORIZON_YEARS
    years = np.arange(2020, 2020 + n)
    ref = np.full(n, 100.0)
    ac_gross_avoided = np.full(n, 10.0)
    ac_penalty = np.full(n, 2.0)
    ac_penalty_with_trees = np.full(n, 1.5)
    tree_avoided = np.full(n, 5.0)
    tree_on_top = np.full(n, 4.0)
    ews_avoided = np.full(n, 3.0)
    combined_gross_avoided = ac_gross_avoided + tree_on_top
    combined_net_avoided = combined_gross_avoided - ac_penalty_with_trees
    ac_feedback_net = ac_gross_avoided - ac_penalty_with_trees

    ac_cost = {
        "capex": np.full(n, 1.0),
        "maintenance": np.full(n, 2.0),
        "electricity": np.full(n, 3.0),
        "electricity_with_trees": np.full(n, 2.5),
        "total": np.full(n, 6.0),
        "total_with_trees": np.full(n, 5.5),
        "pv_capex": np.full(n, 1.0),
        "pv_maintenance": np.full(n, 2.0),
        "pv_electricity": np.full(n, 3.0),
        "pv_electricity_with_trees": np.full(n, 2.5),
        "pv_total": np.full(n, 6.0),
        "pv_total_with_trees": np.full(n, 5.5),
    }
    tree_cost = {
        "capex": np.full(n, 4.0),
        "om": np.full(n, 1.0),
        "total": np.full(n, 5.0),
        "pv_capex": np.full(n, 4.0),
        "pv_om": np.full(n, 1.0),
        "pv_total": np.full(n, 5.0),
    }
    ews_cost = {
        "capex": np.full(n, 1.0),
        "opex_fixed": np.full(n, 2.0),
        "opex_variable": np.full(n, 3.0),
        "total": np.full(n, 6.0),
        "pv_total": np.full(n, 6.0),
    }
    branches = {
        "reference": ref,
        "ac_policy_gross": ref - ac_gross_avoided,
        "ac_policy_net": ref - (ac_gross_avoided - ac_penalty),
        "ac_policy_net_with_tree_feedback": ref - ac_feedback_net,
        "tree_policy": ref - tree_avoided,
        "ews_policy": ref - ews_avoided,
        "ac_tree_policy_gross": ref - combined_gross_avoided,
        "ac_tree_policy_net": ref - combined_net_avoided,
    }
    effects = {
        "ac_gross_avoided_25y": ac_gross_avoided,
        "ac_net_avoided_25y": ac_gross_avoided - ac_penalty,
        "ac_net_with_tree_feedback_25y": ac_feedback_net,
        "ac_tree_gross_avoided_25y": combined_gross_avoided,
        "ac_net_with_trees_25y": combined_net_avoided,
        "ac_penalty_raw_25y": ac_penalty,
        "ac_penalty_with_trees_25y": ac_penalty_with_trees,
        "tree_only_raw_25y": np.full(n, 10.0),
        "trees_on_top_raw_25y": np.full(n, 8.0),
        "tree_maturity_25y": np.full(n, 0.5),
        "tree_avoided_25y": tree_avoided,
        "trees_on_top_25y": tree_on_top,
        "ews_reference_avoided_25y": ews_avoided,
        "lambda_y_25y": np.full(n, 0.75),
    }
    result = {
        "_years_all": years,
        "_policy_branch_annuals": branches,
        "_policy_branch_effects": effects,
        "_ac_cost_streams": ac_cost,
        "_tree_cost_streams": tree_cost,
        "_ews_cost_streams": ews_cost,
        "reference_deaths_25y_cum": ref.sum(),
        "ac_gross_branch_deaths_25y_cum": branches["ac_policy_gross"].sum(),
        "ac_net_branch_deaths_25y_cum": branches["ac_policy_net"].sum(),
        "ac_gross_avoided_deaths_25y_cum": ac_gross_avoided.sum(),
        "ac_net_avoided_deaths_25y_cum": (ac_gross_avoided - ac_penalty).sum(),
        "ac_waste_heat_penalty_25y_cum": ac_penalty.sum(),
        "tree_avoided_deaths_25y_cum": tree_avoided.sum(),
        "tree_on_top_of_ac_avoided_deaths_25y_cum": tree_on_top.sum(),
        "tree_branch_deaths_25y_cum": branches["tree_policy"].sum(),
        "ews_net_avoided_deaths_25y_cum": ews_avoided.sum(),
        "ews_branch_deaths_25y_cum": branches["ews_policy"].sum(),
        "ac_with_trees_gross_avoided_deaths_25y_cum": ac_gross_avoided.sum(),
        "ac_with_trees_net_avoided_deaths_25y_cum": ac_feedback_net.sum(),
        "ac_with_trees_gross_branch_deaths_25y_cum": branches["ac_policy_gross"].sum(),
        "ac_with_trees_net_branch_deaths_25y_cum": branches["ac_policy_net_with_tree_feedback"].sum(),
        "ac_with_trees_waste_heat_penalty_25y_cum": ac_penalty_with_trees.sum(),
        "combined_ac_tree_gross_avoided_deaths_25y_cum": combined_gross_avoided.sum(),
        "combined_ac_tree_net_avoided_deaths_25y_cum": combined_net_avoided.sum(),
        "combined_ac_tree_gross_branch_deaths_25y_cum": branches["ac_tree_policy_gross"].sum(),
        "combined_ac_tree_net_branch_deaths_25y_cum": branches["ac_tree_policy_net"].sum(),
        "combined_ac_tree_pv_cost_25y": ac_cost["pv_total_with_trees"].sum() + tree_cost["pv_total"].sum(),
        "ac_pv_capex_25y": ac_cost["pv_capex"].sum(),
        "ac_pv_maint_25y": ac_cost["pv_maintenance"].sum(),
        "ac_pv_elec_25y": ac_cost["pv_electricity"].sum(),
        "ac_pv_cost_25y": ac_cost["pv_total"].sum(),
        "ac_pv_elec_with_trees_25y": ac_cost["pv_electricity_with_trees"].sum(),
        "ac_pv_cost_with_trees_25y": ac_cost["pv_total_with_trees"].sum(),
        "tree_pv_capex_25y": tree_cost["pv_capex"].sum(),
        "tree_pv_om_25y": tree_cost["pv_om"].sum(),
        "tree_pv_cost_25y": tree_cost["pv_total"].sum(),
        "ews_pv_cost_25y": ews_cost["pv_total"].sum(),
    }
    return runner, result


def test_policy_trajectory_contract_and_mathematical_gate():
    runner, result = _synthetic_runner_and_result()
    qa = runner.validate_sample_output(7, {"ac_ssp": 2, "discount_rate": 0.0}, result)
    assert qa and all(row["status"] == "pass" for row in qa)

    rows = runner.build_policy_trajectory_rows(7, result)
    assert len(rows) == nb09.HORIZON_YEARS * len(nb09.BRANCH_NAMES)
    interaction = next(row for row in rows if row["branch"] == "ac_policy_net_with_tree_feedback")
    combined = next(row for row in rows if row["branch"] == "ac_tree_policy_net")
    assert np.isclose(interaction["annual_cost_eur"], 5.5)
    assert np.isclose(combined["annual_cost_eur"], 10.5)


def test_mathematical_gate_rejects_branch_mismatch():
    runner, result = _synthetic_runner_and_result()
    broken = copy.deepcopy(result)
    broken["_policy_branch_annuals"]["ews_policy"][3] += 1.0
    try:
        runner.validate_sample_output(0, {"ac_ssp": 2, "discount_rate": 0.0}, broken)
    except RuntimeError as exc:
        assert "ews_standalone_provenance" in str(exc)
    else:
        raise AssertionError("The mathematical gate accepted a broken EWS branch.")


def test_campaign_path_isolation_and_nonfinite_checkpoint_roundtrip():
    previous = os.environ.get("NB09_CAMPAIGN_ID")
    try:
        os.environ["NB09_CAMPAIGN_ID"] = "n128_seed42_v3_abc123"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assert nb09._headline_uncertainty_dir(root) == root / "uncertainty_runs" / "n128_seed42_v3_abc123"
        payload = {"finite": 1.0, "pos": np.inf, "neg": -np.inf, "nan": np.nan}
        restored = nb09._json_restore(nb09._json_ready(payload))
        assert restored["pos"] == np.inf
        assert restored["neg"] == -np.inf
        assert np.isnan(restored["nan"])
    finally:
        if previous is None:
            os.environ.pop("NB09_CAMPAIGN_ID", None)
        else:
            os.environ["NB09_CAMPAIGN_ID"] = previous


def test_declared_large_input_fingerprint_and_manifest_reuse():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.bin"
        path.write_bytes(bytes(range(100)))
        old_limit = nb09.INPUT_FULL_HASH_LIMIT_BYTES
        old_windows = nb09.INPUT_SAMPLE_HASH_WINDOWS
        old_window_bytes = nb09.INPUT_SAMPLE_HASH_WINDOW_BYTES
        try:
            nb09.INPUT_FULL_HASH_LIMIT_BYTES = 8
            nb09.INPUT_SAMPLE_HASH_WINDOWS = 4
            nb09.INPUT_SAMPLE_HASH_WINDOW_BYTES = 4
            fingerprint = nb09._input_file_fingerprint(path)
            assert fingerprint["digest_algorithm"] == "sha256-sampled-4x4B-v1"
            assert len(fingerprint["sample_offsets_bytes"]) == 4

            runner = object.__new__(nb09.NB09ImprovedFast)
            runner.root = Path(directory)
            runner.input_files = lambda: [path]
            first = runner.input_fingerprints()
            second = runner.input_fingerprints(first)
            assert second == first
        finally:
            nb09.INPUT_FULL_HASH_LIMIT_BYTES = old_limit
            nb09.INPUT_SAMPLE_HASH_WINDOWS = old_windows
            nb09.INPUT_SAMPLE_HASH_WINDOW_BYTES = old_window_bytes


def test_cost_stream_helpers_preserve_end_of_year_discounting():
    new_users = np.array([2.0, 0.0, 0.0, 0.0])
    stream = nb09._capex_replacement_stream(new_users, 100.0, 2)
    np.testing.assert_allclose(stream, [200.0, 0.0, 200.0, 0.0])
    expected = 200.0 / 1.03 + 200.0 / (1.03**3)
    assert np.isclose(nb09._pv_capex_with_replacements(new_users, 100.0, 2, 0.03), expected)


def test_electricity_feedback_uses_notebook08_maturity_path():
    """The AC-electricity co-benefit must use NB08's direct age ramp."""
    actual = nb09._nb08_electricity_feedback_maturity_factor(
        10,
        12,
        start_age_years=5,
    )
    expected = np.clip((np.arange(10, dtype=float) + 5.0) / 12.0, 0.0, 1.0)
    np.testing.assert_allclose(actual, expected)
    assert np.isclose(actual[0], 5.0 / 12.0)

    # This branch is deliberately not the cohort convolution used by tree
    # mortality benefits and O&M.
    cohort = nb09._cohort_rollout_maturity_factor(10, 12, start_age_years=5)
    assert not np.allclose(actual, cohort)


def test_interpolated_coverage_pattern_preserves_shared_row_index_width():
    """Non-anchor AC maps must stay aligned with row_cols/row_is_city.

    The sparse hazard-column index may contain valid non-city cells, making it
    wider than the strict city mask.  This is the geometry that exposed the
    Bologna smoke-test failure when an alternative AC SSP requested an
    interpolated coverage year.
    """
    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.n_city = 3
    runner.row_is_city = np.array([True, True, True, False, False])
    runner.coverage_pattern_by_mode_year = {
        "base": {
            2020: np.array([1.0, 2.0, 3.0, 0.0, 0.0], dtype=np.float32),
            2030: np.array([3.0, 4.0, 5.0, 0.0, 0.0], dtype=np.float32),
        },
        "policy": {},
    }

    actual = runner.interpolate_coverage_pattern(2025, mode="base")
    expected = np.array([2.0, 3.0, 4.0, 0.0, 0.0], dtype=np.float32)
    np.testing.assert_allclose(actual, expected)
    assert actual.shape == runner.row_is_city.shape

    scaled = nb09._scale_pattern_to_mean_masked(actual, runner.row_is_city, 0.4)
    assert scaled.shape == runner.row_is_city.shape

    try:
        nb09._scale_pattern_to_mean_masked(actual[:-1], runner.row_is_city, 0.4)
    except ValueError as exc:
        assert "Pattern and active-mask shapes must match" in str(exc)
    else:
        raise AssertionError("Masked coverage scaling accepted misaligned arrays.")


def test_alternative_ssp_ac_policy_preserves_notebook05_floor_rule():
    """Alternative AC SSPs may change baseline, never the policy definition."""
    baseline = np.array([0.20, 0.75, 0.40, np.nan], dtype=np.float32)
    targeted = np.array([True, True, False, True])
    policy = nb09._apply_ac_policy_floor(baseline, targeted, 0.70)
    np.testing.assert_allclose(policy[:3], [0.70, 0.75, 0.40])
    assert np.isnan(policy[3])
    assert np.all(policy[np.isfinite(policy)] >= baseline[np.isfinite(baseline)] - 1e-7)

    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.ac_ssp_base = 2
    runner.row_is_city = np.array([True, True, True, False])
    runner.ac_policy_target_mask_rows = np.array([True, False, True, False])
    runner.ac_policy_target_level = 0.70
    runner.coverage_pattern_by_mode_year = {
        "base": {
            2020: np.array([1.0, 2.0, 0.5, 0.0], dtype=np.float32),
            2030: np.array([1.2, 2.2, 0.7, 0.0], dtype=np.float32),
        },
        "policy": {},
    }
    runner.coverage_raw_by_mode_year = {
        "base": {2020: np.array([0.1, 0.2, 0.3, np.nan], dtype=np.float32)},
        "policy": {2020: np.array([0.7, 0.2, 0.7, np.nan], dtype=np.float32)},
    }
    runner.coverage_mean_for_mode = lambda year, ac_ssp, mode="base": 0.40

    alternative_base = runner.coverage_for_sample(2025, 3, mode="base")
    alternative_policy = runner.coverage_for_sample(2025, 3, mode="policy")
    valid = np.isfinite(alternative_base) & np.isfinite(alternative_policy)
    assert np.all(alternative_policy[valid] >= alternative_base[valid] - 1e-7)
    np.testing.assert_allclose(alternative_policy[~runner.ac_policy_target_mask_rows], alternative_base[~runner.ac_policy_target_mask_rows])
    assert np.all(alternative_policy[runner.ac_policy_target_mask_rows] >= 0.70 - 1e-7)

    # The configured SSP remains the immutable deterministic NB05 control.
    np.testing.assert_allclose(
        runner.coverage_for_sample(2020, 2, mode="policy"),
        runner.coverage_raw_by_mode_year["policy"][2020],
        equal_nan=True,
    )

    try:
        nb09._apply_ac_policy_floor(np.ones(2), np.ones(3, dtype=bool), 0.70)
    except ValueError as exc:
        assert "target-mask shapes must match" in str(exc)
    else:
        raise AssertionError("AC policy floor accepted a misaligned target mask.")


def test_alternative_ssp_cost_and_waste_heat_match_spatial_coverage_totals():
    runner = object.__new__(nb09.NB09ImprovedFast)
    coverage = pd.DataFrame(
        {
            "muni_id": [1, 1, 2, 2],
            "year": [2020, 2030, 2020, 2030],
            "pop_muni": [100.0, 110.0, 200.0, 220.0],
            "ac_base_muni": [0.20, 0.30, 0.50, 0.60],
            "ac_policy_muni": [0.70, 0.70, 0.50, 0.60],
        }
    )
    runner.ac_muni_cov_yearly = coverage
    runner.elec_fb_cov_yearly = coverage
    runner.elec_fb_ac_summary = pd.DataFrame(
        {
            "muni_id": [1, 1, 2, 2],
            "year": [2020, 2030, 2020, 2030],
            "kwh_per_user_muni": [100.0, 110.0, 200.0, 210.0],
        }
    )
    runner.elec_fb_dgvi_by_region = {}
    runner.ac_ssp_base = 2
    runner.ac_ssp_options = [1, 2, 3, 5]
    runner.years = [2020, 2030]
    runner._coverage_series_cache = {}
    runner.coverage_mean_for_mode = lambda year, ac_ssp, mode="base": 0.40
    runner.get_kwh_per_user = lambda ac_ssp, year: 150.0
    runner.exposure_path_for_year = lambda year, exp_ssp_idx: Path(f"exposure_{year}_{exp_ssp_idx}")
    runner.coverage_mean_for_exposure = (
        lambda path, year, ac_ssp, mode: 0.65 if mode == "policy" else 0.40
    )
    runner.pop_total_for_exposure = lambda path, scale: 300.0

    base_path = runner.coverage_series_for_waste_heat(
        np.array([2020, 2025, 2030]), 3, mode="base", exp_ssp_idx=0
    )
    policy_path = runner.coverage_series_for_waste_heat(
        np.array([2020, 2025, 2030]), 3, mode="policy", exp_ssp_idx=0
    )
    np.testing.assert_allclose(base_path, 0.40)
    np.testing.assert_allclose(policy_path, 0.65)
    assert np.all(policy_path >= base_path)

    frame = runner.build_muni_ac_cost_frame(
        {"ac_ssp": 3, "EXP_SSP_IDX": 0},
        np.array([2020, 2021]),
        np.array([300.0, 303.0]),
    )
    assert frame is not None
    assert np.all(frame["policy_share_t"] >= frame["base_share_t"] - 1e-7)
    for _, group in frame.groupby("year"):
        weights = group["pop_muni"].to_numpy(float)
        assert np.isclose(np.average(group["base_share_t"], weights=weights), 0.40, atol=1e-6)
        assert np.isclose(np.average(group["policy_share_t"], weights=weights), 0.65, atol=1e-6)


def test_base_ssp_waste_heat_penetration_is_over_the_modelled_area():
    # Two zone cells raised from 0.4 to 0.7, one city cell outside the zones that the policy
    # leaves at the current coverage, and one non-city cell (NaN). Over the area the policy
    # penetration rises by the added users over the area population, not to the zones' 0.7.
    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.ac_ssp_base = 2
    runner._coverage_series_cache = {}
    runner.coverage_years = np.array([2020, 2030])
    base = np.array([[0.4, 0.4], [0.4, np.nan]])
    policy = np.array([[0.7, 0.7], [0.4, np.nan]])
    runner.coverage_full_cube = {"base": np.stack([base, base]), "policy": np.stack([policy, policy])}
    pop = np.array([[100.0, 100.0], [200.0, 50.0]])
    runner.population_grid_like_nb05 = lambda year, exp_ssp_idx: pop

    years = np.array([2020, 2025])
    base_path = runner.coverage_series_for_waste_heat(years, 2, mode="base", exp_ssp_idx=0)
    policy_path = runner.coverage_series_for_waste_heat(years, 2, mode="policy", exp_ssp_idx=0)
    np.testing.assert_allclose(base_path, 0.4)
    np.testing.assert_allclose(policy_path, 0.4 + 60.0 / 400.0)


def test_campaign_design_and_sample_checkpoint_resume():
    with tempfile.TemporaryDirectory() as directory:
        runner = object.__new__(nb09.NB09ImprovedFast)
        runner.unc_dir = Path(directory) / "campaign"
        runner.city = "Test City"
        runner.slug = "test-city"
        runner.param_specs = [nb09.ParamSpec("X", "continuous", low=0.0, high=1.0)]
        runner.input_fingerprints = lambda previous=None: [
            {"path": "input", "size_bytes": 1, "mtime_ns": 1, "digest": "abc"}
        ]
        runner._git_provenance = lambda: {
            "commit": "0123456789abcdef",
            "tracked_worktree_dirty": False,
            "tracked_worktree_status": [],
        }
        runner._lhs_scope = "masselot_headline"
        runner._lhs_scope_family = None
        runner.years = [2020, 2030, 2040, 2050]
        design = pd.DataFrame({"X": [0.25, 0.75]})
        provenance = runner.prepare_campaign(design, n=2, seed=42)
        assert runner.lhs_design_path.exists()
        assert runner.run_manifest_path.exists()
        assert runner.prepare_campaign(design, n=2, seed=42)["campaign_signature"] == provenance["campaign_signature"]

        trajectories = [
            {"sample_idx": 0, "year": year, "branch": branch}
            for year in range(2020, 2020 + nb09.HORIZON_YEARS)
            for branch in nb09.BRANCH_NAMES
        ]
        records = {
            "sample": {"sample_idx": 0, "aai_agg": 1.0},
            "impact": {"sample_idx": 0},
            "cba_ews": {"sample_idx": 0},
            "cba_ac": {"sample_idx": 0},
            "cba_trees": {"sample_idx": 0},
            "vulnerability": {"sample_idx": 0},
            "trajectories": trajectories,
            "qa": [{"sample_idx": 0, "metric": "synthetic", "max_abs_error": 0.0, "status": "pass"}],
        }
        calls = {"count": 0}
        runner.evaluate_sample = lambda row: calls.__setitem__("count", calls["count"] + 1) or {}
        runner._records_for_sample = lambda idx, row, out: records
        first, resumed_first = runner._load_or_evaluate_sample(0, design.iloc[0])
        second, resumed_second = runner._load_or_evaluate_sample(0, design.iloc[0])
        assert not resumed_first and resumed_second
        assert first == second
        assert calls["count"] == 1
        assert provenance["campaign_signature"] == runner.run_provenance["campaign_signature"]

        try:
            runner.prepare_campaign(pd.DataFrame({"X": [0.10, 0.90]}), n=2, seed=42)
        except RuntimeError as exc:
            assert "Existing LHS design differs" in str(exc)
        else:
            raise AssertionError("A different LHS design was allowed to overwrite the campaign.")


def test_central_parity_reports_missing_inputs_without_helper_failure():
    """Exercise the config/artifact comparison helpers without full city data."""
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        runner = object.__new__(nb09.NB09ImprovedFast)
        runner.slug = "test-city"
        runner.city = "Test City"
        runner.out = base / "outputs"
        runner.int_dir = runner.out / "interim"
        runner.tab_dir = runner.out / "tables"
        runner.unc_dir = runner.tab_dir / "uncertainty"
        runner.int_dir.mkdir(parents=True)
        runner.tab_dir.mkdir(parents=True)
        runner.unc_dir.mkdir(parents=True)
        runner.years = [2020, 2030, 2040, 2050]
        runner.ac_cfg = {"capex_per_user": 1000.0}
        runner.trees_cfg = {}
        runner.ews_cfg = {}
        runner.ews_params = {}
        runner.ews_params_path = runner.int_dir / "ews_params_test-city.json"
        (runner.int_dir / "ac_cost_params_test-city.json").write_text(
            '{"capex_per_user": 1000.0}\n'
        )

        try:
            runner.validate_central_against_deterministic({})
        except RuntimeError as exc:
            assert "Central NB09 parity failed" in str(exc)
            assert "missing required deterministic targets" in str(exc)
        else:
            raise AssertionError("Missing deterministic artifacts did not fail central parity.")


def test_assembled_trajectory_and_aggregate_qa_contract():
    runner, result = _synthetic_runner_and_result()
    runner.years = [2020, 2030, 2040, 2050]
    runner.coverage_years = runner.years
    runner.interpolate_coverage_mean = lambda year, mode: 0.3 if mode == "policy" else 0.2
    sample = {key: value for key, value in result.items() if not key.startswith("_")}
    sample.update({"sample_idx": 0, "aai_agg": 100.0, "annual_deaths": 100.0, "ac_added_users_final": 1.0})
    samples = pd.DataFrame([sample])
    trajectories = pd.DataFrame(runner.build_policy_trajectory_rows(0, result))
    mathematical_qa = pd.DataFrame(
        runner.validate_sample_output(0, {"ac_ssp": 2, "discount_rate": 0.0}, result)
    )
    with tempfile.TemporaryDirectory() as directory:
        runner.unc_dir = Path(directory)
        runner.validate_uq_sample_outputs(samples, trajectories, mathematical_qa)
        qa = pd.read_csv(runner.unc_dir / "uq_output_qa_test-city_improved_fast.csv")
        assert set(qa["status"]) == {"ok"}

        broken = trajectories.copy()
        broken.loc[broken.index[0], "avoided_deaths_vs_reference"] = 1.0
        try:
            runner.validate_uq_sample_outputs(samples, broken, mathematical_qa)
        except RuntimeError as exc:
            assert "trajectory_export_contract=identity_failed" in str(exc)
        else:
            raise AssertionError("Aggregate QA accepted a broken trajectory export.")


def _nb05_pick_threshold_closest(values, target):
    """Notebook 05's calibration rule, verbatim (prefer_higher=True)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    uniq = np.unique(v)
    s = np.sort(v)
    counts_ge = len(s) - np.searchsorted(s, uniq, side="left")
    diff = np.abs(counts_ge - target)
    best = np.where(diff == diff.min())[0]
    return float(uniq[best[-1]])


def test_activation_threshold_matches_notebook05_rule():
    rng = np.random.default_rng(0)
    distinct = rng.gamma(2.0, 3.0, size=153)
    assert nb09._activation_threshold(distinct, 23) == _nb05_pick_threshold_closest(distinct, 23)
    assert int((distinct >= nb09._activation_threshold(distinct, 23)).sum()) == 23

    # A cool city: ten in-season days carry heat deaths. A rank statistic
    # returns 0 and activates all 153 days; NB05 activates the ten.
    sparse_deaths = np.zeros(153)
    sparse_deaths[:10] = rng.uniform(1.0, 5.0, size=10)
    threshold = nb09._activation_threshold(sparse_deaths, 23)
    assert threshold == _nb05_pick_threshold_closest(sparse_deaths, 23)
    assert threshold > 0.0
    assert int((sparse_deaths >= threshold).sum()) == 10


def test_central_ews_keeps_the_calibration_day_under_rounding():
    """NB09 reproduces NB05's daily deaths only to ~1e-7 (float32 hazard).

    The deterministic threshold is one of NB05's own daily values, so testing
    NB09's series against it drops the calibration day whenever rounding lands
    below. Rome 2020 under the ERA5-corrected hazard: 22 days instead of 23.
    """
    years = [2020, 2030]
    runner = object.__new__(nb09.NB09ImprovedFast)
    runner.slug = "test-city"
    runner.years = years
    runner.use_extreme_track = False
    runner.ews_warning_trigger_mode = "deaths_threshold"
    runner.ews_threshold_ref_year = 2020
    runner.season_start_md, runner.season_end_md = "05-01", "09-30"
    runner.dates_by_year = {
        y: pd.date_range(f"{y}-01-01", f"{y}-12-31", freq="D").to_numpy(dtype="datetime64[ns]")
        for y in years
    }
    runner.coverage_mean_for_ews = lambda year, ac_ssp, mode="base": 0.0
    runner.ews_init = 0.10
    runner.ews_overlap = {"central": 0.5}
    runner.ews_marg = {age: {"central": 0.10} for age in nb09.AGE_ORDER}
    runner.ews_cf = {"central": 0.35}
    runner.ews_disp = {age: {"central": 0.0} for age in nb09.AGE_ORDER}
    runner.ews_rly = {age: 10 for age in nb09.AGE_ORDER}

    rng = np.random.default_rng(1)
    nb05_daily: dict[int, np.ndarray] = {}
    base_anchor_results: dict[int, dict] = {}
    for y in years:
        dates = runner.dates_by_year[y]
        season = nb09._season_mask_by_md(dates, "05-01", "09-30")
        daily = np.where(season, rng.gamma(2.0, 3.0, size=dates.size), 0.0)
        nb05_daily[y] = daily
        nb09_daily = daily * (1.0 - 1e-7)
        base_anchor_results[y] = {
            "daily_base_total": nb09_daily,
            "age_impacts": {"<15": np.zeros_like(nb09_daily), "15-64": np.zeros_like(nb09_daily), "65+": nb09_daily},
            "season_mask": season,
            "event_day_mask": np.zeros(dates.size, dtype=bool),
            "hazard_citymean_daily": np.zeros(dates.size),
            "pop_total": 1.0,
        }
    season_2020 = base_anchor_results[2020]["season_mask"]
    nb05_threshold = _nb05_pick_threshold_closest(nb05_daily[2020][season_2020], 23)
    assert int((base_anchor_results[2020]["daily_base_total"][season_2020] >= nb05_threshold).sum()) == 22
    # The published artifact is present, as on a real run; NB09 must not test against it.
    runner.ews_cfg = {}
    runner.threshold_meta = {"threshold_deaths_per_day": nb05_threshold}
    runner.threshold_meta_path = "ews_threshold_deaths_test-city.json"

    sample = {
        "_central_control": 1.0,
        "ac_ssp": 2,
        "ews_target_days": 23,
        "ews_recalib_years": 5,
        "ews_ramp_years": 3,
        "ews_overlap_level": "central",
        "ews_interpretation": "marginal",
        "ews_cf_eff_level": "central",
        **{f"ews_eff_{key}_level": "central" for key in ("lt15", "15_64", "65p")},
        **{f"ews_disp_{key}_level": "central" for key in ("lt15", "15_64", "65p")},
    }
    anchors, _, threshold_ref = runner._apply_ews_to_base_anchors(
        sample=sample,
        base_anchor_results=base_anchor_results,
        base_pop_totals={y: 1.0 for y in years},
        ac_mode="base",
    )
    assert anchors[2020]["warning_days"] == 23
    assert np.isclose(threshold_ref, nb05_threshold, rtol=1e-6, atol=0.0)


def test_central_parity_names_a_warning_day_mismatch():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        runner = object.__new__(nb09.NB09ImprovedFast)
        runner.slug = "test-city"
        runner.out = base / "outputs"
        runner.int_dir = runner.out / "interim"
        runner.tab_dir = runner.out / "tables"
        runner.unc_dir = runner.tab_dir / "uncertainty"
        for path in (runner.int_dir, runner.unc_dir):
            path.mkdir(parents=True)
        runner.years = [2020, 2030]
        runner.ac_cfg, runner.trees_cfg, runner.ews_cfg, runner.ews_params = {}, {}, {}, {}
        runner.ews_params_path = runner.int_dir / "absent.json"
        runner.ews_warning_trigger_mode = "deaths_threshold"
        runner.threshold_meta = {"threshold_deaths_per_day": 6.5}
        runner.threshold_meta_path = str(runner.int_dir / "ews_threshold_deaths_test-city.json")
        pd.DataFrame(
            {
                "year": [2020, 2030],
                "scenario": ["central", "central"],
                "baseline_warning_days": [23, 44],
                "net_avoided_deaths": [1.42, 27.8],
            }
        ).to_csv(runner.tab_dir / "annual_heat_deaths_avoided_EWS_test-city.csv", index=False)
        result = {
            "ews_avoided_deaths_2020": 1.42,
            "ews_avoided_deaths_2030": 27.8,
            "ews_warning_days_2020": 22.0,
            "ews_warning_days_2030": 44.0,
            "ews_threshold_ref_deaths_per_day": 6.5 * (1.0 - 1e-7),
        }
        try:
            runner.validate_central_against_deterministic(result)
        except RuntimeError as exc:
            assert "ews_warning_days_2020" in str(exc)
        else:
            raise AssertionError("Central parity accepted a one-day warning-day mismatch.")
        parity = pd.read_csv(runner.unc_dir / "central_parity_test-city_improved_fast.csv").set_index("metric")
        assert parity.loc["ews_warning_days_2020", "status"] == "mismatch"
        assert parity.loc["ews_warning_days_2030", "status"] == "pass"
        assert parity.loc["ews_threshold_ref_deaths_per_day", "status"] == "pass"


def _masselot_main_runner(int_dir: Path, requested: str):
    from cityheat import nb09_improved_fast_masselot_main as nb09mm

    runner = object.__new__(nb09mm.NB09ImprovedFastMasselotMain)
    runner.slug = "test-city"
    runner.int_dir = int_dir
    runner._if_main_family_requested = requested
    runner.if_main_family = nb09mm._strip_varcorr(requested)
    return runner


def test_masselot_bracket_shares_the_canonical_variance_treatment():
    with tempfile.TemporaryDirectory() as directory:
        int_dir = Path(directory)
        manifest = int_dir / "if_family_manifest_test-city.json"

        # Corrected run: the notebook pins IF_MAIN_FAMILY=masselot_tail, NB04
        # built the canonical curves from masselot_tail_varcorr.
        manifest.write_text('{"canonical_source_family": "masselot_tail_varcorr"}')
        for requested in ("masselot_tail", "masselot_tail_varcorr"):
            runner = _masselot_main_runner(int_dir, requested)
            assert runner.if_main_family == "masselot_tail"
            candidates = runner._if_family_candidates()
            assert candidates["masselot"] == [int_dir / "if_curves_by_year_test-city_masselot_varcorr.json"]
            assert candidates["masselot_tail"] == [
                int_dir / "if_curves_by_year_test-city.json",
                int_dir / "if_curves_by_year_test-city_masselot_tail_varcorr.json",
            ]

        # Uncorrected run: unchanged pairing.
        manifest.write_text('{"canonical_source_family": "masselot_tail"}')
        runner = _masselot_main_runner(int_dir, "masselot_tail")
        assert runner._if_family_candidates()["masselot"] == [int_dir / "if_curves_by_year_test-city_masselot.json"]

        # Asking for corrected curves from an uncorrected NB04 run is an error.
        runner = _masselot_main_runner(int_dir, "masselot_tail_varcorr")
        try:
            runner._if_family_candidates()
        except RuntimeError as exc:
            assert "variance-corrected" in str(exc)
        else:
            raise AssertionError("A varcorr request on uncorrected NB04 curves was accepted.")


if __name__ == "__main__":
    test_masselot_bracket_shares_the_canonical_variance_treatment()
    test_activation_threshold_matches_notebook05_rule()
    test_central_ews_keeps_the_calibration_day_under_rounding()
    test_central_parity_names_a_warning_day_mismatch()
    test_policy_trajectory_contract_and_mathematical_gate()
    test_mathematical_gate_rejects_branch_mismatch()
    test_campaign_path_isolation_and_nonfinite_checkpoint_roundtrip()
    test_declared_large_input_fingerprint_and_manifest_reuse()
    test_cost_stream_helpers_preserve_end_of_year_discounting()
    test_electricity_feedback_uses_notebook08_maturity_path()
    test_interpolated_coverage_pattern_preserves_shared_row_index_width()
    test_alternative_ssp_ac_policy_preserves_notebook05_floor_rule()
    test_alternative_ssp_cost_and_waste_heat_match_spatial_coverage_totals()
    test_campaign_design_and_sample_checkpoint_resume()
    test_central_parity_reports_missing_inputs_without_helper_failure()
    test_assembled_trajectory_and_aggregate_qa_contract()
    print("NB09 production contract tests passed.")
