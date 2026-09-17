#!/usr/bin/env bash
# Run one element of the final four-pilot GMD NB09 completion campaign.
# Submit through juno_submit_gmd_nb09_pilots.sh; do not invoke with bsub directly.

#BSUB -J gmd_nb09_pilots
#BSUB -P 0628
#BSUB -q s_long
#BSUB -n 1
#BSUB -M 32G
#BSUB -R "rusage[mem=32G]"
#BSUB -W 1440
#BSUB -o juno_logs/gmd_nb09.%J.%I.out
#BSUB -e juno_logs/gmd_nb09.%J.%I.err

set -euo pipefail

REPO="${URBAN_HEAT_REPO:-/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat}"
VALIDATED_ENGINE_COMMIT="616fbc2d5131eca0e2b375a344b2c9664946c379"
VALIDATED_ENGINE_SHORT="616fbc2d5131"
SEED=42
SCHEMA_TAG="v3"
PILOTS=(athens copenhagen lisbon rome)
SCOPES=(masselot_headline burke_polynomial burke_powerlaw)

cd "$REPO"
mkdir -p juno_logs runs/agnostic_nb09/gmd_pilots

git cat-file -e "${VALIDATED_ENGINE_COMMIT}^{commit}"

# The scripts added after the validated production campaign may change, but
# the scientific engine used by the all-city central-parity gate must not.
ENGINE_PATHS=(
  cityheat/nb09_improved_fast.py
  cityheat/nb09_improved_fast_masselot_main.py
  cityheat/nbsetup_masselot_main.py
)
git diff --quiet "$VALIDATED_ENGINE_COMMIT" HEAD -- "${ENGINE_PATHS[@]}" \
  || { echo "ERROR: NB09 engine differs from validated commit ${VALIDATED_ENGINE_COMMIT}"; exit 1; }
git diff --quiet -- . \
  || { echo "ERROR: unstaged tracked changes under urban-heat; commit and sync first"; exit 1; }
git diff --cached --quiet -- . \
  || { echo "ERROR: staged but uncommitted changes under urban-heat; commit and sync first"; exit 1; }

CENTRAL_MARKER="runs/agnostic_nb09/central_preflight/${SCHEMA_TAG}_${VALIDATED_ENGINE_COMMIT}.ok"
[[ -f "$CENTRAL_MARKER" ]] \
  || { echo "ERROR: validated all-city central gate is missing: $CENTRAL_MARKER"; exit 1; }
[[ "$(sed -n '1p' "$CENTRAL_MARKER")" == "$VALIDATED_ENGINE_COMMIT" ]] \
  || { echo "ERROR: central-gate commit is invalid"; exit 1; }

export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
module load micromamba 2>/dev/null || true
eval "$(micromamba shell hook --shell bash)"
micromamba activate urbanheat
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${GMD_NB09_GATE_ONLY:-0}" == "1" ]]; then
  python - "$REPO" "$SEED" "$VALIDATED_ENGINE_SHORT" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

repo = Path(sys.argv[1])
seed = int(sys.argv[2])
engine_short = sys.argv[3]
pilots = ("athens", "copenhagen", "lisbon", "rome")
specs = (
    ("masselot_headline", 64, f"gmd4_n64_seed{seed}_v3_{engine_short}"),
    ("burke_polynomial", 128, f"gmd4_n128_burke_seed{seed}_v3_{engine_short}"),
    ("burke_powerlaw", 128, f"gmd4_n128_burke_seed{seed}_v3_{engine_short}"),
)
base = repo / "outputs_variants" / "masselot_main_agnostic"
failures: list[str] = []

for scope, n, campaign in specs:
    for city in pilots:
        out = base / city / "tables" / "uncertainty_runs" / campaign
        if scope != "masselot_headline":
            out = out / "burke_sensitivity" / scope
        completion_path = out / "CAMPAIGN_COMPLETE.json"
        manifest_path = out / f"run_manifest_{city}_improved_fast.json"
        samples_path = out / f"unc_samples_{city}_improved_fast.csv"
        sample_qa_path = out / f"sample_mathematical_qa_{city}_improved_fast.csv"
        aggregate_qa_path = out / f"uq_output_qa_{city}_improved_fast.csv"
        trajectories_path = out / f"unc_policy_trajectories_25y_{city}_improved_fast.csv"
        required = (
            completion_path,
            manifest_path,
            samples_path,
            sample_qa_path,
            aggregate_qa_path,
            trajectories_path,
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            failures.append(f"{city}/{scope}: missing {missing}")
            continue
        try:
            completion = json.loads(completion_path.read_text())
            manifest = json.loads(manifest_path.read_text())
            samples = pd.read_csv(samples_path)
            sample_qa = pd.read_csv(sample_qa_path)
            aggregate_qa = pd.read_csv(aggregate_qa_path)
            trajectories = pd.read_csv(trajectories_path)
            expected_scope = "masselot_headline" if scope == "masselot_headline" else "burke_sensitivity"
            expected_family = None if scope == "masselot_headline" else scope
            checks = {
                "completion N": completion.get("n_samples") == n,
                "completion campaign": completion.get("campaign_id") == campaign,
                "manifest N": manifest.get("n_samples") == n,
                "manifest seed": manifest.get("seed") == seed,
                "manifest campaign": manifest.get("campaign_id") == campaign,
                "manifest scope": manifest.get("lhs_scope") == expected_scope,
                "manifest family": manifest.get("lhs_scope_family") == expected_family,
                "sample count": len(samples) == n and samples["sample_idx"].nunique() == n,
                "sample QA": set(sample_qa["status"].astype(str).str.lower()) == {"pass"},
                "aggregate QA": set(aggregate_qa["status"].astype(str).str.lower()) == {"ok"},
                "trajectory count": len(trajectories) == n * 25 * 8,
            }
            critical_artifacts = {
                "samples": samples_path,
                "trajectories": trajectories_path,
                "sample_qa": sample_qa_path,
                "aggregate_qa": aggregate_qa_path,
                "run_manifest": manifest_path,
            }
            recorded_hashes = completion.get("output_sha256", {})
            for key, path in critical_artifacts.items():
                checks[f"hash {key}"] = (
                    recorded_hashes.get(key) == hashlib.sha256(path.read_bytes()).hexdigest()
                )
            bad = [name for name, ok in checks.items() if not ok]
            if bad:
                failures.append(f"{city}/{scope}: failed {bad}")
            else:
                print(f"PASS: {city:<10} {scope:<20} N={n}")
        except Exception as exc:
            failures.append(f"{city}/{scope}: {type(exc).__name__}: {exc}")

if failures:
    print("GMD NB09 FINAL GATE FAILED", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    raise SystemExit(1)

marker = repo / "runs" / "agnostic_nb09" / "gmd_pilots" / f"gmd4_n64_n128_burke_seed{seed}_v3_{engine_short}.ok"
marker.write_text(
    "\n".join(
        [
            "PASS",
            "4/4 updated N=64 Masselot-headline ensembles",
            "4/4 N=128 Burke-polynomial conditional ensembles",
            "4/4 N=128 Burke-powerlaw conditional ensembles",
            f"seed={seed}",
            f"validated_engine={engine_short}",
            "",
        ]
    )
)
print(f"PASS: all 12 GMD pilot scopes are complete and valid; marker={marker}")
PY
  exit 0
fi

[[ -n "${LSB_JOBINDEX:-}" && "$LSB_JOBINDEX" =~ ^[1-9][0-9]*$ ]] \
  || { echo "ERROR: submit this launcher as an LSF array"; exit 1; }

if [[ "${GMD_NB09_SMOKE_ONLY:-0}" == "1" ]]; then
  (( LSB_JOBINDEX >= 1 && LSB_JOBINDEX <= 3 )) \
    || { echo "ERROR: smoke array index must be 1..3"; exit 1; }
  scope="${SCOPES[$((LSB_JOBINDEX - 1))]}"
  city=athens
  n=8
  campaign="gmd_smoke_n8_seed${SEED}_v3_${VALIDATED_ENGINE_SHORT}"
  export GMD_NB09_SMOKE=1
else
  (( LSB_JOBINDEX >= 1 && LSB_JOBINDEX <= 12 )) \
    || { echo "ERROR: production array index must be 1..12"; exit 1; }
  scope_index=$(( (LSB_JOBINDEX - 1) / 4 ))
  city_index=$(( (LSB_JOBINDEX - 1) % 4 ))
  scope="${SCOPES[$scope_index]}"
  city="${PILOTS[$city_index]}"
  if [[ "$scope" == "masselot_headline" ]]; then
    n=64
    campaign="gmd4_n64_seed${SEED}_v3_${VALIDATED_ENGINE_SHORT}"
  else
    n=128
    campaign="gmd4_n128_burke_seed${SEED}_v3_${VALIDATED_ENGINE_SHORT}"
  fi
  export GMD_NB09_SMOKE=0
fi

export NB09_N="$n"
export NB09_SEED="$SEED"
export NB09_OUTPUT_SCHEMA_TAG="$SCHEMA_TAG"
export NB09_CAMPAIGN_ID="$campaign"
export URBAN_HEAT_OUTPUT_VARIANT=masselot_main_agnostic
export IF_MAIN_FAMILY=masselot_tail
export HAZARD_TRACK=standard
export NB09_BURKE_SENSITIVITY=0
export NB09_REQUIRE_BOTH_MASSELOT=1

echo "==== $(date -Iseconds) | task ${LSB_JOBINDEX} | city=$city | scope=$scope | N=$n | campaign=$campaign ===="

# A transient kernel/filesystem failure gets two automatic resumptions. Valid
# sample checkpoints are reused, so a retry does not repeat completed samples.
max_attempts="${GMD_NB09_MAX_ATTEMPTS:-3}"
for attempt in $(seq 1 "$max_attempts"); do
  echo "Attempt ${attempt}/${max_attempts}: $city $scope"
  if python scripts/run_gmd_nb09_scope.py \
      --city "$city" --scope "$scope" --n "$n" --seed "$SEED"; then
    echo "PASS: $city $scope completed"
    exit 0
  else
    rc=$?
  fi
  echo "WARNING: attempt ${attempt} failed with code ${rc}; checkpoints will be reused" >&2
  if (( attempt < max_attempts )); then
    sleep 60
  fi
done

echo "ERROR: $city $scope failed after ${max_attempts} attempts" >&2
exit 1
