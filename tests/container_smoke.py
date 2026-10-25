"""Optional live-MQTT test inside the image, using only a private pseudo-terminal.

Run with the MQTT environment, /data on a disposable test volume and /tmp writable.
No real serial device is used. The production energy volume must not be mounted.
"""

import json
import os
import signal
import struct
import subprocess
import time
import tty
from pathlib import Path


def crc(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value.to_bytes(2, "little")


def main():
    assert os.getuid() == 10001
    assert not Path("/dev/ttyUSB0").exists()
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    env = os.environ | {"SERIAL_PORT": os.ttyname(slave)}
    state = Path(env.get("STATE_FILE", "/data/energy-offsets.json"))
    health = Path(env.get("HEALTH_FILE", "/tmp/ksem-health.json"))
    assert not state.exists(), "Use a new disposable volume, never production state"
    baseline = None
    results = []
    try:
        for cycle in range(2):
            with subprocess.Popen(["python", "/app/ksemServer.py"], env=env) as process:
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("Runtime exited unexpectedly")
                        if health.exists():
                            status = json.loads(health.read_text())
                            if status.get("healthy") and status.get("serial_running"):
                                break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("No healthy complete measurement")
                    subprocess.run(["python", "-m", "ksem_emulator.healthcheck"], check=True)
                    saved = json.loads(state.read_text())
                    if baseline is None:
                        baseline = saved["base"]
                    assert saved["base"] == baseline
                    body = struct.pack(">BBHH", 1, 3, 0, 2)
                    os.write(master, body + crc(body))
                    reply = b""
                    deadline = time.monotonic() + 2
                    while len(reply) < 9 and time.monotonic() < deadline:
                        try:
                            reply += os.read(master, 1024)
                        except BlockingIOError:
                            time.sleep(0.01)
                    assert len(reply) == 9 and reply[:3] == bytes.fromhex("010304"), reply.hex()
                    assert crc(reply[:-2]) == reply[-2:]
                    power = int.from_bytes(reply[3:7], "big") / 10
                    assert power >= 0
                    results.append({"cycle": cycle + 1, "power_w": power, "healthcheck": True})
                finally:
                    process.send_signal(signal.SIGTERM)
                    assert process.wait(timeout=5) == 0
            assert not json.loads(health.read_text())["healthy"]
        print(
            json.dumps(
                {
                    "uid": os.getuid(),
                    "real_serial_device_opened": False,
                    "persistent_offsets_preserved": True,
                    "checks": results,
                }
            )
        )
    finally:
        os.close(master)
        os.close(slave)


if __name__ == "__main__":
    main()
