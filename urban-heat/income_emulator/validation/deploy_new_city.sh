set -euo pipefail
# One-shot deployment of the income emulator to a NEW (unlabelled) city:
#   OSM boundary  ->  covariates + predict p_inc (deploy_predict)  ->  choropleth + ranked table.
# Run from income_emulator/ in the urbanheat env. config_local.yaml must point at the local
# GHSL + LCZ rasters (see config_local.template.yaml).
#
#   bash validation/deploy_new_city.sh "Berlin, Germany"   10 Berlin DE
#   bash validation/deploy_new_city.sh "Muenchen, Germany"  9 Munich DE
#   PYTHON=/path/to/python bash validation/deploy_new_city.sh "Warsaw, Poland" 9 Warsaw PL config_local.yaml
#
# Args: <"Place, Country"> <OSM admin_level> <City> <ISO2> [config=config_local.yaml]

PLACE="${1:?usage: deploy_new_city.sh \"Place, Country\" ADMIN_LEVEL City ISO2 [config]}"
LEVEL="${2:?missing OSM admin_level (e.g. 9 or 10)}"
CITY="${3:?missing city name}"
CC="${4:?missing ISO2 country code}"
CFG="${5:-config_local.yaml}"
PY="${PYTHON:-/opt/anaconda3/envs/urbanheat/bin/python}"

SLUG="$(printf '%s' "$CITY" | tr '[:upper:] ' '[:lower:]_')"
GPKG="data/${CC}_${SLUG}.gpkg"
LAYER="${SLUG}_units"
PRED="validation/results/deploy_${SLUG}.csv"
MAP="validation/results/${SLUG}_p_inc_map.png"

echo ">> [1/3] boundary from OSM  (${PLACE}, admin_level=${LEVEL})"
"$PY" validation/build_boundary_osm.py --place "$PLACE" --admin-level "$LEVEL" \
      --city "$CITY" --out "$GPKG" --layer "$LAYER"

echo ">> [2/3] covariates + predict p_inc  ->  ${PRED}"
"$PY" deploy_predict.py --boundary "$GPKG" --layer "$LAYER" --key boundary_code \
      --city "$CITY" --country "$CC" --exclude "$CITY" --config "$CFG" --out "$PRED"

echo ">> [3/3] choropleth + ranked table  ->  ${MAP}"
"$PY" validation/map_and_rank.py --pred "$PRED" --boundary "$GPKG" --layer "$LAYER" \
      --title "$CITY" --out "$MAP"

echo "done. predictions: ${PRED}  |  map: ${MAP}"
