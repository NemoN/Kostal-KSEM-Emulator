"""Victron input. Only N subscriptions and R read requests are used; never W."""

import asyncio
import json
import logging
import time
import uuid

import paho.mqtt.client as mqtt

from .snapshot import number

LOG = logging.getLogger(__name__)
PHASE_FIELDS = {"Power": "power", "Voltage": "voltage", "Current": "current", "PowerFactor": "pf"}
REQUIRED = {"Connected", "Ac/Frequency", "Ac/Energy/Forward"} | {
    f"Ac/L{phase}/{name}" for phase in (1, 2, 3) for name in [*PHASE_FIELDS, "Energy/Forward"]
}


class VictronMqttSource:
    def __init__(self, config, store, clock=time.monotonic):
        self.config, self.store, self.clock = config, store, clock
        self.values, self.dirty = {}, set()
        self.connected = False
        self.loop = None
        self.client = None
        self.stopped = False

    def reset(self, reason):
        self.values.clear()
        self.dirty.clear()
        self.store.invalidate(reason)

    def feed(self, topic, payload, retained=False):
        prefix = self.config.source_id + "/"
        if not self.connected or not topic.startswith(prefix) or retained:
            return
        key = topic[len(prefix) :]
        if key not in REQUIRED:
            return
        try:
            value = number(json.loads(payload)["value"])
            if key == "Connected" and value != 1:
                raise ValueError("Victron meter is disconnected")
            now = self.clock()
            self.values[key] = (value, now)
            self.dirty.add(key)
            if not REQUIRED.issubset(self.dirty):
                return
            oldest = min(self.values[k][1] for k in REQUIRED)
            if now - oldest > self.config.stale_seconds:
                self.store.invalidate("Incomplete or stale MQTT measurement")
                return
            data = {
                "emeters": [
                    {name: self.values[f"Ac/L{phase}/{field}"][0] for field, name in PHASE_FIELDS.items()}
                    for phase in (1, 2, 3)
                ],
                "frequency": self.values["Ac/Frequency"][0],
                "energy_kwh": {
                    "total": self.values["Ac/Energy/Forward"][0],
                    **{f"L{p}": self.values[f"Ac/L{p}/Energy/Forward"][0] for p in (1, 2, 3)},
                },
            }
            self.store.set(data, observed_at=oldest)
            self.dirty.clear()
        except (ValueError, KeyError, TypeError, OSError, OverflowError):
            self.reset("Invalid MQTT measurement or energy counter; inspect source and state")

    def _dispatch(self, callback, *args):
        if not self.stopped:
            self.loop.call_soon_threadsafe(callback, *args)

    def _ready(self):
        self.reset("Waiting for new MQTT measurement")
        self.connected = True
        self.request("keepalive")

    def _lost(self):
        self.connected = False
        self.reset("MQTT disconnected")

    def request(self, suffix, payload=""):
        # Deliberately no general-purpose publish interface or settings writes.
        return self.client.publish(self.config.topic("R", suffix), payload=payload, qos=1, retain=False)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self._dispatch(self._lost)
            return
        client.subscribe(self.config.source_id + "/#", qos=1)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        self._dispatch(self._lost if any(code.is_failure for code in reason_codes) else self._ready)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._dispatch(self._lost)

    def _on_message(self, client, userdata, message):
        self._dispatch(self.feed, message.topic, message.payload, message.retain)

    async def run_forever(self):
        self.loop = asyncio.get_running_loop()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ksem-" + uuid.uuid4().hex[:12])
        if self.config.mqtt_username:
            self.client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.connect_async(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
        self.client.loop_start()
        last_keepalive = self.clock()
        try:
            while True:
                if self.connected:
                    self.request(self.config.meter + "/Ac")
                    self.request(self.config.meter + "/Connected")
                    if self.clock() - last_keepalive >= 30:
                        self.request("keepalive", json.dumps({"keepalive-options": ["suppress-republish"]}))
                        last_keepalive = self.clock()
                await asyncio.sleep(1)
        finally:
            self.stopped = True
            self.connected = False
            self.store.invalidate("MQTT source stopped")
            self.client.disconnect()
            await asyncio.to_thread(self.client.loop_stop)
