import copy
import json

import pytest
from conftest import mqtt_values
from pymodbus.constants import ExcCodes

from ksem_emulator.energy import EnergyOffsets
from ksem_emulator.mqtt_source import REQUIRED
from ksem_emulator.server import KsemContext
from ksem_emulator.snapshot import ENERGY_REGISTERS, SnapshotStore


def feed(source, data, retained=False, skip=()):
    for key, value in mqtt_values(data).items():
        if key not in skip:
            source.feed(source.config.source_id + "/" + key, json.dumps({"value": value}), retained)


def u32(words):
    return (words[0] << 16) | words[1]


def u64(words):
    result = 0
    for word in words:
        result = (result << 16) | word
    return result


def test_register_addresses_units_and_measured_frequency(measurement):
    store = SnapshotStore()
    store.set(measurement)
    device = KsemContext(store)
    assert u32(device.getValues(3, 0, 2)) == 6000
    assert u32(device.getValues(3, 2, 2)) == 0
    assert u32(device.getValues(3, 40, 2)) == 1000
    assert u32(device.getValues(3, 80, 2)) == 2000
    assert u32(device.getValues(3, 120, 2)) == 3000
    assert u32(device.getValues(3, 60, 2)) == 1000
    assert u32(device.getValues(3, 62, 2)) == 230000
    assert u32(device.getValues(3, 64, 2)) == 435
    assert u32(device.getValues(3, 26, 2)) == 49987
    assert device.getValues(3, 8244, 1) == [1000]
    assert device.getValues(3, 8249, 1) == [7]
    assert u64(device.getValues(3, 8245, 4)) > 1_700_000_000_000


def test_import_export_netting_and_atomic_publication(measurement):
    store = SnapshotStore()
    measurement["emeters"][0]["power"] = -1000
    store.set(measurement)
    old = store.read()
    device = KsemContext(store)
    assert u32(device.getValues(3, 0, 2)) == 0
    assert u32(device.getValues(3, 2, 2)) == 5000
    assert u32(device.getValues(3, 42, 2)) == 10000
    measurement["emeters"][0]["power"] = 100
    store.set(measurement)
    assert u32(old[3:5]) == 5000  # Previously returned immutable snapshot remains intact.
    assert u32(device.getValues(3, 0, 2)) == 6000


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "100", 500_000_000])
def test_bad_measurement_never_becomes_zero_or_wraps(measurement, bad):
    store = SnapshotStore()
    store.set(measurement)
    measurement["emeters"][0]["power"] = bad
    with pytest.raises((ValueError, TypeError, OverflowError)):
        store.set(measurement)
    assert store.read() is None
    assert KsemContext(store).getValues(3, 0, 2) == ExcCodes.DEVICE_FAILURE


def test_missing_phase_field_invalidates_existing_measurement(measurement):
    store = SnapshotStore()
    store.set(measurement)
    del measurement["emeters"][1]["current"]
    with pytest.raises(KeyError):
        store.set(measurement)
    assert store.read() is None


def test_energy_offsets_persist_and_do_not_change_on_reset(tmp_path, measurement):
    path = tmp_path / "offsets.json"
    store = SnapshotStore(offsets=EnergyOffsets(path, "source"))
    store.set(measurement)
    device = KsemContext(store)
    for address in ENERGY_REGISTERS.values():
        assert u64(device.getValues(3, address, 4)) == 0
    measurement["energy_kwh"] = {k: v + 0.1234 for k, v in measurement["energy_kwh"].items()}
    store.set(measurement)
    for address in ENERGY_REGISTERS.values():
        assert u64(device.getValues(3, address, 4)) == 1234
    restored = SnapshotStore(offsets=EnergyOffsets(path, "source"))
    restored.set(measurement)
    assert u64(KsemContext(restored).getValues(3, 512, 4)) == 1234
    before = path.read_bytes()
    measurement["energy_kwh"]["L2"] = 0
    with pytest.raises(ValueError, match="decreased"):
        restored.set(measurement)
    assert path.read_bytes() == before
    assert restored.read() is None
    with pytest.raises(ValueError, match="different source"):
        EnergyOffsets(path, "other")


def test_missing_energy_registers_and_writes_are_rejected(measurement):
    store = SnapshotStore()
    store.set(measurement)
    device = KsemContext(store)
    assert device.getValues(3, 512, 4) == ExcCodes.ILLEGAL_ADDRESS
    assert device.getValues(3, 516, 4) == ExcCodes.ILLEGAL_ADDRESS
    assert device.getValues(3, 148, 1) == ExcCodes.ILLEGAL_ADDRESS
    assert device.setValues(6, 0, [1]) == ExcCodes.ILLEGAL_FUNCTION
    assert device.getValues(1, 0, 1) == ExcCodes.ILLEGAL_FUNCTION


def test_mqtt_requires_whole_new_packet_after_reconnect(mqtt_pair, measurement):
    source, store, now = mqtt_pair
    feed(source, measurement, skip=("Connected",))
    assert store.read() is None
    source.feed(source.config.source_id + "/Connected", '{"value":1}')
    assert store.read() is not None
    old = store.read()
    changed = copy.deepcopy(measurement)
    changed["emeters"][0]["power"] = 999
    feed(source, changed, skip=("Connected",))
    assert store.read() is old
    source.feed(source.config.source_id + "/Connected", '{"value":1}')
    assert u32(KsemContext(store).getValues(3, 0, 2)) == 14990
    source._lost()
    source.connected = True
    assert store.read() is None
    source.feed(source.config.source_id + "/Connected", '{"value":1}')
    assert store.read() is None


def test_mqtt_steady_values_refresh_but_missing_updates_expire(mqtt_pair, measurement):
    source, store, now = mqtt_pair
    feed(source, measurement)
    now[0] += 4
    feed(source, measurement)  # Receipt, not value changes, determines freshness.
    now[0] += 4
    assert store.read() is not None
    now[0] += 1.001
    assert store.read() is None


@pytest.mark.parametrize("payload", ['{"value":null}', "{}", "", '{"value":"bad"}', '{"value":NaN}'])
def test_mqtt_content_fault_invalidates_immediately(mqtt_pair, measurement, payload):
    source, store, now = mqtt_pair
    feed(source, measurement)
    source.feed(source.config.source_id + "/Ac/L1/Power", payload)
    assert store.read() is None
    assert not source.values


def test_mqtt_retained_values_and_disconnected_meter_are_not_accepted(mqtt_pair, measurement):
    source, store, now = mqtt_pair
    feed(source, measurement, retained=True)
    assert store.read() is None
    feed(source, measurement)
    source.feed(source.config.source_id + "/Connected", '{"value":0}')
    assert store.read() is None


def test_oldest_required_field_controls_freshness(mqtt_pair, measurement):
    source, store, now = mqtt_pair
    values = mqtt_values(measurement)
    for key in sorted(REQUIRED):
        if key == "Connected":
            now[0] += 4
        source.feed(source.config.source_id + "/" + key, json.dumps({"value": values[key]}))
    assert store.read() is not None
    now[0] += 1.01
    assert store.read() is None


def test_requests_only_use_read_topics_qos_one_and_no_retain(mqtt_pair):
    source, store, now = mqtt_pair

    class FakeClient:
        def __init__(self):
            self.calls = []

        def publish(self, topic, **kwargs):
            self.calls.append((topic, kwargs))

    source.client = FakeClient()
    source._ready()
    source.request(source.config.meter + "/Ac")
    source.request(source.config.meter + "/Connected")
    assert len(source.client.calls) == 3
    for topic, kwargs in source.client.calls:
        assert topic.startswith("bridge/R/test-portal/")
        assert kwargs["qos"] == 1 and kwargs["retain"] is False
