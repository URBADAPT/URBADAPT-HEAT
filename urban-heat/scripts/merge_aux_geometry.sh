#!/usr/bin/env bash
# Carry the district geometry into the corrected tree, so it can serve as AUX_BASE.
#
# Usage:  bash scripts/merge_aux_geometry.sh [--dry-run]
#
# The Nature Cities R pipeline takes ONE AUX_BASE, and _helpers.R's rule is that it must
# be the interim companion of the same run as OUTPUTS_BASE - pointed at a different run
# it silently mixes two. The corrected tree satisfies that for five of the six patterns
# the figures read, but carries no district *.gpkg, which is the fig4 input. Last time
# that file was missing, fig4 skipped every exemplar without saying so.
#
# Geometry is the one class of input that is genuinely run-invariant: administrative
# boundaries do not depend on the hazard, the impact functions or the calibration. So it
# is carried over rather than regenerated, and this script exists to make that a recorded
# decision rather than a stray copy. It writes GEOMETRY_PROVENANCE.txt beside the tree
# naming the source, and it never overwrites a file that is already there.

set -uo pipefail

SRC="${SRC:-/g/Il mio Drive/adaptation_infrastructure_database/juno_pull/n128_interim_for_figures}"
DST="${DST:-/c/Users/giaco/OneDrive - IIASA/Documents/juno/corrected40}"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1

[[ -d "$SRC" ]] || { echo "FATAL: no source tree at $SRC"; exit 1; }
[[ -d "$DST" ]] || { echo "FATAL: no destination tree at $DST"; exit 1; }

copied=0; skipped=0; missing=0
for d in "$DST"/*/; do
  c=$(basename "$d")
  [[ -d "$SRC/$c/interim" ]] || { missing=$((missing+1)); continue; }
  mkdir -p "$d/interim"
  while IFS= read -r f; do
    b=$(basename "$f")
    if [[ -f "$d/interim/$b" ]]; then skipped=$((skipped+1)); continue; fi
    if (( DRY )); then
      echo "  would copy $c/$b"
    else
      cp -p "$f" "$d/interim/$b" || { echo "  FAILED $c/$b"; continue; }
    fi
    copied=$((copied+1))
  done < <(find "$SRC/$c/interim" -maxdepth 1 -name '*.gpkg')
done

echo "copied $copied, already present $skipped, cities without a source interim $missing"

if (( DRY == 0 && copied > 0 )); then
  cat > "$DST/GEOMETRY_PROVENANCE.txt" <<EOF
District geometry (*.gpkg under <city>/interim/) was NOT produced by this run.

It was carried over on $(date +%Y-%m-%d) from:
  $SRC

Everything else in this tree is from the both-corrections run (NB02-08, Oct 2026);
confirm per city with interim/hazard_manifest.json -> era5_bias_correction.applied,
which must read true.

Geometry is carried over rather than regenerated because administrative boundaries do
not depend on the hazard, the impact functions or the calibration. No other input class
may be mixed between runs this way.

NOTE: tables/uncertainty_*/ in this tree is STALE - NB09 was never run for any
calibration arm, so those files are inherited from an earlier run (Aug-Sep 2026) and
must not be reported as corrected. Nothing in the R pipeline reads them today.
EOF
  echo "wrote $DST/GEOMETRY_PROVENANCE.txt"
fi
