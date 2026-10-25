"""Container runtime and a live-source check that never opens a serial port."""

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import time

from pymodbus.server import ModbusSerialServer

from shellyPoller import ShellyPoller

from .config import Config
from .energy import EnergyOffsets, atomic_json
from .mqtt_source import VictronMqttSource
from .server import server_context
from .snapshot import SnapshotStore


def source_for(config, store):
    if config.source_type == "victron_mqtt":
        return VictronMqttSource(config, store)
    return ShellyPoller(
        config.shelly_url,
        config.shelly_username,
        config.shelly_password,
        store,
        interval=0.1,
        timeout=2,
        log_enabled=False,
    )


async def run(config, check=False, duration=8):
    offsets = (
        EnergyOffsets(None if check else config.state_file, config.source_id)
        if config.source_type == "victron_mqtt"
        else None
    )
    store = SnapshotStore(config.stale_seconds, offsets)
    source = asyncio.create_task(source_for(config, store).run_forever())
    server = None
    try:
        if check:
            deadline, first = time.monotonic() + 30, None
            while time.monotonic() < deadline:
                if source.done():
                    source.result()
                    raise RuntimeError("Source stopped")
                if store.read() is not None:
                    first = first or time.monotonic()
                    if time.monotonic() - first >= duration:
                        data = store.get()
                        print(
                            json.dumps(
                                {
                                    "healthy": True,
                                    "phases_w": [p["power"] for p in data["emeters"]],
                                    "power_w": sum(p["power"] for p in data["emeters"]),
                                    "frequency_hz": data.get("frequency", 50),
                                    "serial_opened": False,
                                    "persistent_state_written": False,
                                },
                                allow_nan=False,
                            )
                        )
                        return
                elif first is not None:
                    raise RuntimeError("Source became invalid during check: " + str(store.status()))
                await asyncio.sleep(0.1)
            raise RuntimeError("No complete fresh measurement within 30 seconds: " + str(store.status()))
        server = ModbusSerialServer(
            server_context(store),
            port=config.serial_port,
            baudrate=38400,
            bytesize=8,
            parity="N",
            stopbits=2,
            timeout=1,
        )
        await server.serve_forever(background=True)
        logging.info("KSEM RTU started: device=1 baud=38400 format=8N2 source=%s", config.source_type)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        previous = None
        while not stop.is_set():
            if source.done():
                source.result()
                raise RuntimeError("Source stopped")
            status = store.status()
            serial_running = server.is_active()
            status.update(updated_at=time.time(), serial_running=serial_running)
            if not serial_running:
                status.update(healthy=False, error="Serial transport disconnected")
            atomic_json(config.health_file, status)
            if status["healthy"] != previous:
                logging.info(
                    "Measurement status: %s", "healthy" if status["healthy"] else status["error"] or "stale"
                )
                previous = status["healthy"]
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), 1)
    finally:
        if server is not None:
            await server.shutdown()
        source.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await source
        if not check:
            atomic_json(
                config.health_file, {"healthy": False, "serial_running": False, "updated_at": time.time()}
            )


def main():
    parser = argparse.ArgumentParser(description="KSEM RTU emulator: Shelly or Victron MQTT")
    parser.add_argument(
        "--check", action="store_true", help="Check live input without serial or persistent energy state"
    )
    parser.add_argument("--duration", type=float, default=8, help="Fresh seconds required by --check (1-20)")
    args = parser.parse_args()
    if not 1 <= args.duration <= 20:
        parser.error("duration must be between 1 and 20 seconds")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(Config.from_env(), args.check, args.duration))
    except (ValueError, OSError, RuntimeError) as exc:
        logging.error("KSEM stopped: %s", exc)
        raise SystemExit(1) from None
