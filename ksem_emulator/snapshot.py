"""Validate complete measurements and atomically publish their encoded registers."""

import copy
import math
import threading
import time

from .registers import build_runtime_registers_from_snapshot, split_u32, split_u64

ENERGY_REGISTERS = {"total": 512, "L1": 592, "L2": 672, "L3": 752}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Measurement must be a finite number")
    return float(value)


def validate_snapshot(data):
    meters = data.get("emeters", [])
    if len(meters) != 3:
        raise ValueError("Three complete phases are required")
    total_p = total_s = 0
    for meter in meters:
        p, u, i, pf = (number(meter[k]) for k in ("power", "voltage", "current", "pf"))
        if u < 0 or i < 0 or abs(pf) > 1:
            raise ValueError("Invalid voltage, current or power factor")
        total_p += p
        total_s += u * i
        for raw in [abs(p) * 10, u * 1000, i * 1000, u * i * 10]:
            if round(raw) >= 2**32:
                raise ValueError("Measurement exceeds KSEM range")
    frequency = number(data.get("frequency", 50.0))
    if frequency <= 0 or round(frequency * 1000) >= 2**32:
        raise ValueError("Invalid frequency")
    if any(round(v * 10) >= 2**32 for v in (abs(total_p), total_s)):
        raise ValueError("Total power exceeds KSEM range")


class SnapshotStore:
    def __init__(self, stale_seconds=5.0, offsets=None, clock=time.monotonic):
        self.stale_seconds, self.offsets, self.clock = stale_seconds, offsets, clock
        self._lock = threading.RLock()
        self._registers = None
        self._data = None
        self._observed = None
        self._error = "Waiting for a complete measurement"
        self.energy_registers = False

    def invalidate(self, reason):
        with self._lock:
            self._error = reason
            self._registers = None

    def set(self, data, observed_at=None):
        try:
            validate_snapshot(data)
            values = build_runtime_registers_from_snapshot(data)
            values[27:29] = split_u32(round(data.get("frequency", 50.0) * 1000))
            energy = data.get("energy_kwh")
            encoded_energy = (
                self.offsets.relative(energy) if energy is not None and self.offsets is not None else None
            )
            if encoded_energy is not None:
                for key, address in ENERGY_REGISTERS.items():
                    values[address + 1 : address + 5] = split_u64(encoded_energy[key])
            with self._lock:
                self._data = copy.deepcopy(data)
                self._registers = tuple(values)
                self._observed = self.clock() if observed_at is None else observed_at
                self.energy_registers = encoded_energy is not None
                self._error = None
        except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
            self.invalidate(str(exc))
            raise

    def read(self):
        with self._lock:
            if (
                self._registers is None
                or self._observed is None
                or self.clock() - self._observed > self.stale_seconds
            ):
                return None
            return self._registers

    def age_seconds(self):
        with self._lock:
            return None if self._observed is None else self.clock() - self._observed

    def has_fresh_data(self, max_age_seconds):
        age = self.age_seconds()
        return self.read() is not None and age is not None and age <= max_age_seconds

    def get(self):
        with self._lock:
            return copy.deepcopy(self._data)

    def status(self):
        with self._lock:
            return {
                "healthy": self.read() is not None,
                "age_seconds": self.age_seconds(),
                "error": self._error,
            }
