#!/usr/bin/env bash
# Fetch UrbClim daily-mean T2M for named cities, so the curve-level convolution check can
# cover climate classes it currently misses.
#
# Usage:  bash scripts/fetch_urbclim.sh <City> [City ...]
#   e.g.  bash scripts/fetch_urbclim.sh Budapest Vienna
#
# City names are the VITO/PROVIDE spellings (capitalised), which is also what
# data/<City>/ uses. ~30 MB per year, 2008-2017, so ~300 MB per city.
#
# Resumable and safe to re-run: a year is skipped only when the local file is already
# plausibly complete. Size-checking matters here - an interrupted download leaves a
# short file that a mere existence test would accept, and a truncated year would then
# be silently dropped by the 365-day guard in build_variance_corrected_ifs.py rather
# than re-fetched.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="https://provide.marvin.vito.be/ftp/compressed_daily"
YEARS="${YEARS:-2008 2009 2010 2011 2012 2013 2014 2015 2016 2017}"
MIN_BYTES="${MIN_BYTES:-5000000}"   # a real year is tens of MB; anything smaller is partial

[[ $# -ge 1 ]] || { echo "usage: bash scripts/fetch_urbclim.sh <City> [City ...]"; exit 2; }

for CITY in "$@"; do
  DIR="$ROOT/data/$CITY/UrbClim/UrbClimT2Mmean"
  mkdir -p "$DIR"
  echo "=== $CITY -> $DIR"
  got=0; skip=0; fail=0
  for Y in $YEARS; do
    F="$DIR/T2M_year_daily_mean_$Y.nc"
    if [[ -f "$F" ]] && [[ "$(stat -c%s "$F" 2>/dev/null || echo 0)" -ge "$MIN_BYTES" ]]; then
      skip=$((skip+1)); continue
    fi
    rm -f "$F"
    if curl -sS -m 1800 --retry 3 --retry-delay 5 -o "$F" \
         "$BASE/$CITY/T2M_year_daily_mean_$Y.nc" \
       && [[ "$(stat -c%s "$F" 2>/dev/null || echo 0)" -ge "$MIN_BYTES" ]]; then
      got=$((got+1)); printf "  %s ok (%.0f MB)\n" "$Y" "$(stat -c%s "$F" | awk '{print $1/1048576}')"
    else
      rm -f "$F"; fail=$((fail+1)); echo "  $Y FAILED"
    fi
  done
  echo "  fetched $got, already had $skip, failed $fail"
  [[ $fail -gt 0 ]] && echo "  re-run to retry the failures"
done

echo
echo "next: python validation/test_convolved_if.py    # now covers the new cities"
