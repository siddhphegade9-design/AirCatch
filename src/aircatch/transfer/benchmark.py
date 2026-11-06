"""Measure end-to-end transfer speed (includes SHA-256 on both sides and disk writes).

  python -m aircatch.transfer.benchmark --size-mb 512

NOTE: this uses localhost, so it measures your CPU and disk, NOT your Wi-Fi.
For real Wi-Fi speed, use the cli `send`/`receive` between two devices.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from aircatch.transfer.receiver import IncomingOffer, Receiver
from aircatch.transfer.sender import send_file
from aircatch.transfer.util import human_size


async def run(size_mb: int) -> int:
    async def accept_all(offer: IncomingOffer) -> bool:
        return True

    with tempfile.TemporaryDirectory(prefix="aircatch-bench-") as tmp:
        source = Path(tmp) / "bench.bin"
        block = os.urandom(1024 * 1024)
        with open(source, "wb") as f:
            for _ in range(size_mb):
                f.write(block)
        receiver = Receiver(Path(tmp) / "received", accept_all, device_name="bench")
        port = await receiver.start("127.0.0.1", 0)
        try:
            result = await send_file("127.0.0.1", port, source, sender_name="bench")
        finally:
            await receiver.stop()
        if not result.ok:
            print(f"FAILED: {result.reason}")
            return 1
        saved = Path(tmp) / "received" / result.saved_as
        same_size = saved.stat().st_size == source.stat().st_size
        print(f"Sent {human_size(result.size)} in {result.seconds:.2f}s "
              f"= {result.mb_per_second:.0f} MB/s (localhost)")
        print(f"SHA-256 verified by receiver: yes | size matches: {same_size}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-mb", type=int, default=256)
    return asyncio.run(run(parser.parse_args().size_mb))


if __name__ == "__main__":
    raise SystemExit(main())
