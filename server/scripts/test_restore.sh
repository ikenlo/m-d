#!/usr/bin/env bash
# Репетиція відновлення: свіжа копія → тимчасова тека → цілісність і кількість рядків.
# Використання (з каталогу server/): ./scripts/test_restore.sh [шлях-до-копії]
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-}"
if [ -z "$FILE" ]; then
  FILE="$(python -m mds backup | tail -1)"
fi
python -m mds restore-check "$FILE"
echo "OK: копію $FILE можна відновити"
