#!/usr/bin/env bash
# Download the MariaDB Wikipedia datasets for the challenge.
#
#   ./download_datasets.sh [small|medium|large]
#
# Sources:
#   https://github.com/MariaDB/mariadb-wikipedia-datasets        (datasets)
#   https://github.com/MariaDB/mariadb-vector-wikipedia-rag       (fi/sv/en starter repo)
set -euo pipefail

SIZE="${1:-small}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/data"
mkdir -p "$DEST"

echo "==> Downloading MariaDB Wikipedia dataset repositories (size preset: $SIZE)"
if [ ! -d "$DEST/mariadb-wikipedia-datasets" ]; then
  git clone --depth 1 https://github.com/MariaDB/mariadb-wikipedia-datasets.git \
    "$DEST/mariadb-wikipedia-datasets"
fi
if [ ! -d "$DEST/mariadb-vector-wikipedia-rag" ]; then
  git clone --depth 1 https://github.com/MariaDB/mariadb-vector-wikipedia-rag.git \
    "$DEST/mariadb-vector-wikipedia-rag"
fi

echo "==> Datasets ready under: $DEST"
echo "    Inspect the downloaded layout, then run:"
echo "    python db/load/import_dump.py --data-dir $DEST"
echo
echo "NOTE: the import script expects *.jsonl files whose records carry"
echo "      lang / title / text (+ optional page_id, qid/wikidata_qid, revision_id)."
echo "      If the downloaded layout differs (e.g. SQL dumps or TSV), adapt"
echo "      db/load/import_dump.py — it is intentionally small so this stays"
echo "      a 15-minute task."
