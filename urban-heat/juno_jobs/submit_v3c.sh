#!/usr/bin/env bash
# v3c: rerun NB05-NB08 in place (masselot_main_v3 / masselot_main_gmd_v3) after the AC heat-flux
# waste heat and hazard-scaled AC electricity (fd01949), then NB09 for each city chained on that
# city's own NB05-08 job id. Cities already marked done in runs/v3c_* are skipped by v3_group.lsf.
#   bash juno_jobs/submit_v3c.sh            # 40 cities + 4 GMD pilots
#   bash juno_jobs/submit_v3c.sh rome milan # a subset of the 40 (GMD skipped)
# Memory: NB05-08 = ceil(1.3 x the city's NB04-08 peak of 2026-10-16), at least 3G (Paris: its
# NB04 was killed at 8G, so 12G); NB09 = ceil(1.3 x the city's v2 NB09 peak), at least 2G.
set -uo pipefail
REPO=/work/cmcc/gf31024/URBADAPT-HEAT/urban-heat
cd "$REPO" || exit 1
git diff --quiet -- . && git diff --cached --quiet -- . || { echo "FATAL: tracked changes"; exit 1; }
echo "git $(git rev-parse --short HEAD)"

# city  NB04-08 peak MB  NB09 peak MB
read -r -d '' TABLE <<'EOF'
amsterdam 4119 3711
athens 4234 3687
barcelona 2977 2563
berlin 7568 7019
bologna 2462 2224
bratislava 1801 1571
brussels 4740 4413
bucharest 4285 3814
budapest 4702 4143
cologne 4597 4302
copenhagen 2133 1827
dublin 3866 3101
hamburg 4752 4541
helsinki 3870 3189
lisbon 3795 3201
ljubljana 2026 1783
lyon 5356 4479
madrid 4730 4436
marseille 2747 2479
milan 2472 2292
munich 4238 4083
nantes 2537 2366
naples 2157 1834
palermo 1901 1624
paris 9000 7576
porto 3381 3105
prague 4398 3893
riga 3073 2537
rome 5043 4329
rotterdam 3487 3359
sevilla 2302 2084
sofia 2255 2051
stockholm 3307 2905
tallinn 1988 1637
thessaloniki 2048 1774
varna 1665 1345
vienna 4267 3925
vilnius 3821 3117
warsaw 4724 4131
zagreb 2542 2283
EOF

gb() { local g=$(( ($1 * 13 + 10239) / 10240 )); (( g < $2 )) && g=$2; echo "$g"; }
jobid() { grep -o 'Job <[0-9]*>' | grep -o '[0-9]*'; }

want=" ${*:-} "
while read -r city mb_nb mb_09; do
  [[ -z "$city" ]] && continue
  [[ "$want" != "  " && "$want" != *" $city "* ]] && continue
  m1=$(gb "$mb_nb" 3); m2=$(gb "$mb_09" 2)
  j1=$(SRC=masselot_main_v3 DST=masselot_main_v3 STATE=v3c_40 CITIES="$city" NOTEBOOKS="05 06 07 08" \
       bsub -J "v3c_$city" -M "${m1}G" -R "rusage[mem=${m1}G]" -W 3:00 < juno_jobs/v3_group.lsf 2>/dev/null | jobid)
  [[ -z "$j1" ]] && { echo "$city: NB05-08 submit FAILED"; continue; }
  j2=$(BURKE=0 VARIANT=masselot_main_v3 CITY="$city" \
       bsub -J "nb09v3c_$city" -M "${m2}G" -R "rusage[mem=${m2}G]" -w "done($j1)" \
       < scripts/juno_run_nb09_gmd.lsf 2>/dev/null | jobid)
  echo "$city  NB05-08 $j1 (${m1}G)  NB09 $j2 (${m2}G)"
done <<< "$TABLE"

[[ "$want" != "  " ]] && exit 0

# GMD pilots: city  NB04-08 peak MB  NB09 request G
while read -r city mb_nb g09; do
  m1=$(gb "$mb_nb" 3)
  j1=$(SRC=masselot_main_gmd_v3 DST=masselot_main_gmd_v3 STATE=v3c_gmd CITIES="$city" NOTEBOOKS="05 06 07 08" \
       bsub -J "v3cg_$city" -M "${m1}G" -R "rusage[mem=${m1}G]" -W 3:00 < juno_jobs/v3_group.lsf 2>/dev/null | jobid)
  [[ -z "$j1" ]] && { echo "GMD $city: NB05-08 submit FAILED"; continue; }
  a=$(VARIANT=masselot_main_gmd_v3 CITY="$city" \
      bsub -J "nb09gc_$city" -M "${g09}G" -R "rusage[mem=${g09}G]" -w "done($j1)" \
      < scripts/juno_run_nb09_gmd.lsf 2>/dev/null | jobid)
  b=$(BURKE=0 NB09_N=64 VARIANT=masselot_main_gmd_v3 CITY="$city" \
      bsub -J "nb09gc64_$city" -M "${g09}G" -R "rusage[mem=${g09}G]" -w "done($j1)" \
      < scripts/juno_run_nb09_gmd.lsf 2>/dev/null | jobid)
  echo "GMD $city  NB05-08 $j1 (${m1}G)  NB09 N128+Burke $a  N64 $b (${g09}G)"
done <<'EOF'
rome 5043 6
athens 4229 5
lisbon 3816 5
copenhagen 2099 3
EOF
