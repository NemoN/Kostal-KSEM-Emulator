"""Real RTU frames on an isolated pseudo-terminal; no physical device is opened."""

import asyncio
import os
import struct
import tty

from pymodbus.server import ModbusSerialServer

from ksem_emulator.server import server_context
from ksem_emulator.snapshot import SnapshotStore


def crc(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value.to_bytes(2, "little")


async def test_real_serial_frames_valid_stale_pnp_and_write_rejection(measurement):
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    store = SnapshotStore()
    store.set(measurement)
    server = ModbusSerialServer(
        server_context(store), port=os.ttyname(slave), baudrate=38400, bytesize=8, parity="N", stopbits=2
    )
    try:
        await server.serve_forever(background=True)
        await asyncio.sleep(0.05)

        async def request(function, address, count, response_size):
            body = struct.pack(">BBHH", 1, function, address, count)
            if address == 0 and function == 3:
                assert crc(body) == bytes.fromhex("c40b")
            os.write(master, body + crc(body))
            received = b""
            async with asyncio.timeout(2):
                while len(received) < response_size:
                    try:
                        received += os.read(master, 1024)
                    except BlockingIOError:
                        await asyncio.sleep(0.005)
            assert crc(received[:-2]) == received[-2:]
            return received[:-2]

        assert await request(3, 0, 2, 9) == bytes.fromhex("01030400001770")
        assert await request(3, 8244, 1, 7) == bytes.fromhex("01030203e8")
        assert await request(6, 0, 1, 5) == bytes.fromhex("018601")
        store.invalidate("test MQTT outage")
        assert await request(3, 0, 2, 5) == bytes.fromhex("018304")
        store.set(measurement)
        assert await request(3, 0, 2, 9) == bytes.fromhex("01030400001770")
    finally:
        await server.shutdown()
        os.close(master)
        os.close(slave)
