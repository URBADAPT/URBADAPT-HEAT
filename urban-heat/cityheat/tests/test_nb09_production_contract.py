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


if __name__ == "__main__":
    test_policy_trajectory_contract_and_mathematical_gate()
    test_mathematical_gate_rejects_branch_mismatch()
    test_campaign_path_isolation_and_nonfinite_checkpoint_roundtrip()
    test_declared_large_input_fingerprint_and_manifest_reuse()
    test_cost_stream_helpers_preserve_end_of_year_discounting()
    test_campaign_design_and_sample_checkpoint_resume()
    test_central_parity_reports_missing_inputs_without_helper_failure()
    test_assembled_trajectory_and_aggregate_qa_contract()
    print("NB09 production contract tests passed.")
