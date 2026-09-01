#!/usr/bin/env bash
# Full pipeline, replayable end to end.
#   TARGET_DATE=20260826 ./scripts/run_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export TARGET_DATE="${TARGET_DATE:-$(date +%Y%m%d)}"
PY=.venv/bin/python
echo "### date cible: $TARGET_DATE"

# 0) Disk space (the pipeline peaks at ~25 GB)
avail=$(df -g . | awk 'NR==2{print $4}')
[ "$avail" -ge 25 ] || { echo "ERREUR: ${avail} Go libres, 25 Go requis"; exit 1; }

# 1) Python environment
[ -d .venv ] || uv venv --python 3.11 .venv
uv pip install -q --python $PY -r requirements.txt

# 2) Sources
mkdir -p data/raw data/processed logs
cd data/raw
[ -f sncf-national.zip ] || curl -L --fail --retry 3 -o sncf-national.zip \
  https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip
[ -f transilien.zip ] || curl -L --fail --retry 3 -o transilien.zip \
  https://eu.ftp.opendatasoft.com/sncf/gtfs/transilien-gtfs.zip
# The France PBF (4.7 GB) only exists to produce data/processed/france-rail.osm.pbf
# (14 MB). Once that exists, switching date needs no download at all: the rail
# network does not depend on the day.
if [ ! -f ../processed/france-rail.osm.pbf ] && [ ! -f france-latest.osm.pbf ]; then
  curl -L --fail --retry 3 -o france-latest.osm.pbf.md5 \
    https://download.geofabrik.de/europe/france-latest.osm.pbf.md5
  curl -L --fail --retry 3 -C - -o france-latest.osm.pbf \
    https://download.geofabrik.de/europe/france-latest.osm.pbf
  [ "$(md5 -q france-latest.osm.pbf)" = "$(awk '{print $1}' france-latest.osm.pbf.md5)" ] \
    || { echo "ERREUR: checksum PBF invalide"; exit 1; }
fi
rm -rf sncf-national transilien
for z in sncf-national transilien; do
  # zip-slip guard: reject any absolute or parent-traversing entry
  if unzip -Z1 "$z.zip" | grep -qE '^/|(^|/)\.\./'; then
    echo "ERREUR: $z.zip contient un chemin suspect, extraction annulee"; exit 1
  fi
  unzip -oq "$z.zip" -d "$z"
done
sha256sum sncf-national.zip transilien.zip > gtfs-checksums.txt 2>/dev/null || \
  shasum -a 256 sncf-national.zip transilien.zip > gtfs-checksums.txt
echo "empreintes des feeds GTFS du jour -> data/raw/gtfs-checksums.txt" 
cd ../..

# 3) Inspection
$PY scripts/gtfs_inspect.py | tee logs/inspection.txt

# 4) pfaedle (native build if missing)
if [ ! -x vendor/pfaedle/build/pfaedle ]; then
  # Pinned commit: cloning HEAD would make the result depend on whatever is
  # upstream that day, and would run unreviewed code
  PFAEDLE_REV=99f2cd466696ecc6bdb73b2b3bb9008557fcb84a
  mkdir -p vendor && git clone --recurse-submodules \
    https://github.com/ad-freiburg/pfaedle.git vendor/pfaedle
  git -C vendor/pfaedle checkout -q "$PFAEDLE_REV"
  git -C vendor/pfaedle submodule update --init --recursive
  cmake -S vendor/pfaedle -B vendor/pfaedle/build -DCMAKE_BUILD_TYPE=Release
  make -C vendor/pfaedle/build -j"$(sysctl -n hw.ncpu)"
fi

# 5) Feed merge + map-matching (slow: backgrounded with a log)
rm -rf data/processed/merged data/processed/gtfs-shaped
$PY scripts/merge_gtfs.py
nohup bash scripts/run_pfaedle.sh > logs/pfaedle.log 2>&1 &
wait $!

# 6) Precompute + verification
$PY scripts/precompute.py | tee logs/precompute.txt
$PY scripts/verify.py     | tee logs/verify.txt

echo "### termine — servir avec: ./scripts/serve.sh"
