#!/bin/bash
set -euo pipefail

if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
  /opt/venv/bin/python manage.py migrate --noinput
fi

if [[ "${WAIT_FOR_TEMPORAL:-0}" == "1" ]]; then
  : "${TEMPORAL_ADDRESS:?TEMPORAL_ADDRESS must be set when WAIT_FOR_TEMPORAL=1}"
  /opt/venv/bin/python - <<'PY'
import os
import socket
import time

address = os.environ["TEMPORAL_ADDRESS"]
if ":" in address:
    host, port = address.rsplit(":", 1)
    port = int(port)
else:
    host, port = address, 7233

for attempt in range(1, 121):
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"Temporal is reachable at {host}:{port}")
            break
    except OSError as exc:
        print(f"Waiting for Temporal ({attempt}/120): {exc}")
        time.sleep(2)
else:
    raise SystemExit(f"Temporal not reachable at {host}:{port}")
PY
fi

exec "$@"
