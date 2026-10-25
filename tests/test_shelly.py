"""Exercise the retained Shelly poller against a real local HTTP server."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from ksem_emulator.server import KsemContext
from ksem_emulator.snapshot import SnapshotStore
from shellyPoller import ShellyPoller


async def test_original_shelly_input_remains_usable(measurement):
    payload = {"emeters": measurement["emeters"]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/status"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = SnapshotStore()
    source = ShellyPoller(
        f"http://127.0.0.1:{server.server_port}/status",
        "admin",
        "test",
        store,
        interval=0.02,
        log_enabled=False,
    )
    task = asyncio.create_task(source.run_forever())
    try:
        async with asyncio.timeout(2):
            while store.read() is None:
                await asyncio.sleep(0.01)
        assert KsemContext(store).getValues(3, 0, 2) == [0, 6000]
        assert KsemContext(store).getValues(3, 26, 2) == [0, 50000]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()
