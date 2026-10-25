"""Docker healthcheck; no network connections or device access."""

import json
import os
import time
from pathlib import Path


def main():
    try:
        data = json.loads(Path(os.getenv("HEALTH_FILE", "/tmp/ksem-health.json")).read_text())
        healthy = data["healthy"] and data["serial_running"] and 0 <= time.time() - data["updated_at"] <= 5
    except (ValueError, KeyError, OSError):
        healthy = False
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
