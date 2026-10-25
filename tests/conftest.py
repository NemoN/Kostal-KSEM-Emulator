import copy

import pytest

from ksem_emulator.config import Config
from ksem_emulator.energy import EnergyOffsets
from ksem_emulator.mqtt_source import REQUIRED, VictronMqttSource
from ksem_emulator.snapshot import SnapshotStore


@pytest.fixture
def measurement():
    return {
        "emeters": [
            {"power": 100.0, "voltage": 230.0, "current": 1.0, "pf": 0.435},
            {"power": 200.0, "voltage": 231.0, "current": 2.0, "pf": 0.433},
            {"power": 300.0, "voltage": 232.0, "current": 3.0, "pf": 0.431},
        ],
        "frequency": 49.987,
        "energy_kwh": {"total": 6000.0, "L1": 1000.0, "L2": 2000.0, "L3": 3000.0},
    }


@pytest.fixture
def mqtt_pair(tmp_path):
    now = [100.0]
    config = Config(source_type="victron_mqtt", portal_id="test-portal", mqtt_prefix="bridge")
    store = SnapshotStore(
        offsets=EnergyOffsets(tmp_path / "energy.json", config.source_id), clock=lambda: now[0]
    )
    source = VictronMqttSource(config, store, clock=lambda: now[0])
    source.connected = True
    return source, store, now


def mqtt_values(data):
    result = {
        "Connected": 1,
        "Ac/Frequency": data["frequency"],
        "Ac/Energy/Forward": data["energy_kwh"]["total"],
    }
    for phase, meter in enumerate(data["emeters"], 1):
        for field, key in [
            ("Power", "power"),
            ("Voltage", "voltage"),
            ("Current", "current"),
            ("PowerFactor", "pf"),
        ]:
            result[f"Ac/L{phase}/{field}"] = meter[key]
        result[f"Ac/L{phase}/Energy/Forward"] = data["energy_kwh"][f"L{phase}"]
    assert set(result) == REQUIRED
    return copy.deepcopy(result)
