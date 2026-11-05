#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../server"
python -m mds init --username margaryta --full-name "Маргарита" --password Director123
python -c "from mds.__main__ import bootstrap_catalog; from mds.config import Config; print('послуг:', bootstrap_catalog(Config()))"
python -m mds create-user dmitry --full-name "Дмитро" --role master --password Master123
python -m mds demo-fop1
