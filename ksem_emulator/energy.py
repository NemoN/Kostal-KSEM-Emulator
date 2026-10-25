"""Persist meter offsets and reject source counter resets instead of inventing energy."""

import json
import math
import os
import tempfile
from pathlib import Path

KEYS = {"total", "L1", "L2", "L3"}


def validate_counters(values):
    if set(values) != KEYS:
        raise ValueError("Incomplete energy counters")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in values.values()
    ):
        raise ValueError("Invalid energy counter")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class EnergyOffsets:
    def __init__(self, path, source_id):
        self.path = Path(path) if path is not None else None
        self.source_id = source_id
        self.base = None
        self.last = None
        if self.path is not None and self.path.exists():
            state = json.loads(self.path.read_text())
            if state.get("version") != 1 or state.get("source") != source_id:
                raise ValueError("Energy state belongs to a different source or version")
            validate_counters(state["base"])
            validate_counters(state["last"])
            if any(state["last"][k] < state["base"][k] for k in KEYS):
                raise ValueError("Invalid persisted energy offsets")
            self.base, self.last = state["base"], state["last"]

    def relative(self, values):
        validate_counters(values)
        base = self.base if self.base is not None else values
        previous = self.last if self.last is not None else values
        if any(values[k] < previous[k] for k in KEYS):
            raise ValueError("Source energy counter decreased; preserve state and inspect the meter")
        # Source is kWh; KSEM encodes unsigned 64-bit units of 0.1 Wh.
        result = {k: round((values[k] - base[k]) * 10000) for k in KEYS}
        if any(not 0 <= v < 2**64 for v in result.values()):
            raise ValueError("Energy counter exceeds KSEM range")
        if self.path is not None and values != self.last:
            atomic_json(self.path, {"version": 1, "source": self.source_id, "base": base, "last": values})
        self.base, self.last = dict(base), dict(values)
        return result
