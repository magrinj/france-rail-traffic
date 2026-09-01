#!/usr/bin/env bash
# Map-matching of trips onto the rail network.
#
# Two ways to obtain pfaedle, depending on context:
#   - locally compiled binary (vendor/pfaedle/build/pfaedle), preferred
#   - pinned Docker image via PFAEDLE_IMAGE (the CI path)
# One script therefore serves both local and runner, with no branch in the caller.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x vendor/pfaedle/build/pfaedle ]; then
  PF=(./vendor/pfaedle/build/pfaedle -c vendor/pfaedle/pfaedle.cfg)
elif [ -n "${PFAEDLE_IMAGE:-}" ]; then
  # --user keeps output files from being owned by root
  PF=(docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/w -w /w "$PFAEDLE_IMAGE")
else
  echo "ERREUR: aucun pfaedle disponible. Compiler dans vendor/pfaedle/, ou definir" >&2
  echo "        PFAEDLE_IMAGE=ghcr.io/ad-freiburg/pfaedle@sha256:<digest>" >&2
  exit 1
fi

# 1) Filter the France PBF (4.7 GB) down to the rail network the feed needs.
#    The 14 MB result does not depend on the date, so it is never recomputed.
if [ ! -f data/processed/france-rail.osm.pbf ]; then
  "${PF[@]}" -x data/raw/france-latest.osm.pbf -m rail \
      -X data/processed/france-rail.osm.pbf data/processed/merged
fi

# 2) Map-matching against the filtered PBF
"${PF[@]}" -D -m rail -x data/processed/france-rail.osm.pbf \
    --stats -d data/processed \
    -o data/processed/gtfs-shaped data/processed/merged

# pfaedle writes stats.json where precompute.py writes its own: move it aside
mv -f data/processed/stats.json data/processed/pfaedle_stats.json
