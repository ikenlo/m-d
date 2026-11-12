#!/bin/sh
set -e
DATA="${MDS_DATA_DIR:-${RAILWAY_VOLUME_MOUNT_PATH:-/app/server/data}}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA"
  chown -R mds:mds "$DATA"
  exec setpriv --reuid=mds --regid=mds --init-groups "$@"
fi
exec "$@"
