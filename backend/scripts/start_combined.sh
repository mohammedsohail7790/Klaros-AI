#!/bin/bash
# Single-container entrypoint for the "everything in one Render service"
# deployment: Temporal server (auto-setup) + backend API + Event Worker +
# Temporal Worker, all in one process group.
#
# Not used by the standard backend/Dockerfile — that one runs only
# `uvicorn app.main:app`, unchanged. This script exists only for
# Dockerfile.combined.
set -eu -o pipefail

echo "[combined] starting Temporal server (auto-setup)..."
TEMPORAL_ADDRESS="0.0.0.0:7233" /etc/temporal/entrypoint.sh autosetup &
TEMPORAL_PID=$!

echo "[combined] waiting for Temporal to accept connections on 127.0.0.1:7233..."
for i in $(seq 1 60); do
  if (echo > /dev/tcp/127.0.0.1/7233) >/dev/null 2>&1; then
    echo "[combined] Temporal is up after ${i}s"
    break
  fi
  sleep 1
done

export TEMPORAL_HOST="127.0.0.1:7233"

echo "[combined] starting backend API..."
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &

echo "[combined] starting Event Worker..."
python -m app.events.worker &

echo "[combined] starting Temporal Worker..."
python -m app.workers.main &

# If any process dies, exit so Render restarts the whole container rather
# than silently limping along with a piece missing.
wait -n
echo "[combined] a process exited — shutting down container"
kill "${TEMPORAL_PID}" 2>/dev/null || true
exit 1
