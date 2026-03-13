#!/bin/sh
# Fix permissions on config_rw volume -- Docker creates it owned by root,
# but the container runs as appuser (uid 1001).
# This script runs as root, fixes ownership, then drops to appuser.
set -e

mkdir -p /app/config_rw
chown -R appuser:appuser /app/config_rw
chmod 755 /app/config_rw

exec gosu appuser "$@"
