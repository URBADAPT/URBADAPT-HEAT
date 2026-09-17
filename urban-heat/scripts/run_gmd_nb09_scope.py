#!/usr/bin/env python3
"""Run one isolated NB09 scope for the four-city GMD uncertainty appendix.

This deliberately bypasses the convenience wrapper that always evaluates the
Masselot headline before the optional Burke scopes.  The final corrected
N=128 Masselot headline already exists for all 40 cities, so the GMD completion
campaign only needs:

* an updated N=64 Masselot-headline ensemble for each pilot; and
* two N=128 IF-conditional Burke ensembles for each pilot.

Every call still uses the production NB09 engine, central mathematical control,
sample checkpoints, full trajectory export, PAWN outputs, aggregate QA, and
the durable CAMPAIGN_COMPLETE marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from cityheat.nb09_improved_fast import OUTPUT_SCHEMA_VERSION
from cityheat.nb09_improved_fast_masselot_main import NB09ImprovedFastMasselotMain


PILOT_CITIES = ("athens", "copenhagen", "lisbon", "rome")
SCOPES = ("masselot_headline", "burke_polynomial", "burke_powerlaw")


def validate_completed_run(
    output_dir: Path,
    *,
    city: str,
    campaign_id: str,
    scope: str,
    n: int,
    seed: int,
) -> None:
    """Fail unless the just-finished scope has complete, internally valid outputs."""
    completion_path = output_dir / "CAMPAIGN_COMPLETE.json"
    manifest_path = output_dir / f"run_manifest_{city}_improved_fast.json"
    samples_path = output_dir / f"unc_samples_{city}_improved_fast.csv"
    lhs_path = output_dir / f"lhs_design_{city}_improved_fast.csv"
    sample_qa_path = output_dir / f"sample_mathematical_qa_{city}_improved_fast.csv"
    aggregate_qa_path = output_dir / f"uq_output_qa_{city}_improved_fast.csv"
    trajectories_path = output_dir / f"unc_policy_trajectories_25y_{city}_improved_fast.csv"

    required = (
        completion_path,
        manifest_path,
        samples_path,
        lhs_path,
        sample_qa_path,
        aggregate_qa_path,
        trajectories_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing required completed-run artifacts: {missing}")

    with manifest_path.open() as handle:
        manifest = json.load(handle)
    expected_scope = "masselot_headline" if scope == "masselot_headline" else "burke_sensitivity"
    expected_family = None if scope == "masselot_headline" else scope
    expected_manifest = {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "slug": city,
        "n_samples": int(n),
        "seed": int(seed),
        "lhs_scope": expected_scope,
        "lhs_scope_family": expected_family,
    }
    mismatches = {
        key: (manifest.get(key), expected)
        for key, expected in expected_manifest.items()
        if manifest.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"Run-manifest mismatch in {manifest_path}: {mismatches}")

    samples = pd.read_csv(samples_path)
    lhs = pd.read_csv(lhs_path)
    sample_qa = pd.read_csv(sample_qa_path)
    aggregate_qa = pd.read_csv(aggregate_qa_path)
    trajectories = pd.read_csv(trajectories_path)

    if len(samples) != n or samples["sample_idx"].nunique() != n:
        raise RuntimeError(f"Expected {n} unique samples, found {len(samples)} rows")
    if len(lhs) != n or lhs["sample_idx"].nunique() != n:
        raise RuntimeError(f"Expected {n} LHS rows, found {len(lhs)} rows")
    if set(sample_qa["status"].astype(str).str.lower()) != {"pass"}:
        raise RuntimeError("At least one per-sample mathematical QA row did not pass")
    if set(aggregate_qa["status"].astype(str).str.lower()) != {"ok"}:
        raise RuntimeError("At least one aggregate uncertainty QA row is not ok")

    expected_trajectory_rows = n * 25 * 8
    if len(trajectories) != expected_trajectory_rows:
        raise RuntimeError(
            f"Expected {expected_trajectory_rows} trajectory rows, found {len(trajectories)}"
        )

    with completion_path.open() as handle:
        completion = json.load(handle)
    if completion.get("n_samples") != n or completion.get("campaign_id") != campaign_id:
        raise RuntimeError(f"Invalid completion marker: {completion_path}")
    critical_artifacts = {
        "samples": samples_path,
        "trajectories": trajectories_path,
        "sample_qa": sample_qa_path,
        "aggregate_qa": aggregate_qa_path,
        "lhs_design": lhs_path,
        "run_manifest": manifest_path,
    }
    recorded_hashes = completion.get("output_sha256", {})
    for key, path in critical_artifacts.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if recorded_hashes.get(key) != actual:
            raise RuntimeError(f"Completion-marker hash mismatch for {key}: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True, choices=PILOT_CITIES)
    parser.add_argument("--scope", required=True, choices=SCOPES)
    parser.add_argument("--n", required=True, type=int)
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    smoke = os.environ.get("GMD_NB09_SMOKE", "0").strip() == "1"
    if smoke:
        if args.n != 8:
            raise SystemExit("The GMD pre-production smoke workflow is locked to N=8")
    else:
        if args.scope == "masselot_headline" and args.n != 64:
            raise SystemExit("The GMD completion workflow permits only N=64 for masselot_headline")
        if args.scope != "masselot_headline" and args.n != 128:
            raise SystemExit("The GMD Burke-conditional workflows require N=128")

    campaign_id = os.environ.get("NB09_CAMPAIGN_ID", "").strip()
    if not campaign_id:
        raise SystemExit("NB09_CAMPAIGN_ID must be set explicitly by the Juno launcher")

    # Freeze the canonical four-city runtime interpretation.
    os.environ["URBAN_HEAT_OUTPUT_VARIANT"] = "masselot_main_agnostic"
    os.environ["IF_MAIN_FAMILY"] = "masselot_tail"
    os.environ["HAZARD_TRACK"] = "standard"
    os.environ["NB09_BURKE_SENSITIVITY"] = "0"
    os.environ["NB09_REQUIRE_BOTH_MASSELOT"] = "1"

    runner = NB09ImprovedFastMasselotMain(args.city)
    if args.scope == "masselot_headline":
        runner.set_lhs_scope("masselot_headline", require_both_masselot=True)
    else:
        runner.set_lhs_scope("burke_sensitivity", family=args.scope)

    print(
        f"GMD NB09 scope: city={args.city}; scope={args.scope}; "
        f"N={args.n}; seed={args.seed}; campaign={campaign_id}; output={runner.unc_dir}",
        flush=True,
    )
    runner.run(n=args.n, seed=args.seed, make_figures=False)
    validate_completed_run(
        runner.unc_dir,
        city=args.city,
        campaign_id=campaign_id,
        scope=args.scope,
        n=args.n,
        seed=args.seed,
    )
    print(f"PASS: complete and valid {args.scope} run for {args.city}", flush=True)


if __name__ == "__main__":
    main()
