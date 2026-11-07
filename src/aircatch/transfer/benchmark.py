"""Measure end-to-end transfer speed (includes SHA-256 on both sides and disk writes).

  python transfer_bench.py --size-mb 512            # unencrypted
  python transfer_bench.py --size-mb 512 --secure   # TLS 1.3 encrypted, mutual authentication

NOTE: this uses localhost, so it measures your CPU and disk, NOT your Wi-Fi.
For real Wi-Fi speed, use the cli `send`/`receive` between two devices.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from aircatch.security.identity import load_or_create
from aircatch.security.trust import TrustedPeer, TrustStore
from aircatch.transfer.receiver import IncomingOffer, Receiver
from aircatch.transfer.sender import send_file
from aircatch.transfer.util import human_size


async def run(size_mb: int, secure: bool) -> int:
    async def accept_all(offer: IncomingOffer) -> bool:
        return True

    with tempfile.TemporaryDirectory(prefix="aircatch-bench-") as tmp:
        tmp_path = Path(tmp)
        source = tmp_path / "bench.bin"
        block = os.urandom(1024 * 1024)
        with open(source, "wb") as f:
            for _ in range(size_mb):
                f.write(block)

        extra, send_kwargs = {}, {}
        if secure:
            rx_id = load_or_create(tmp_path / "rx", "bench-receiver")
            tx_id = load_or_create(tmp_path / "tx", "bench-sender")
            rx_trust = TrustStore(tmp_path / "rx" / "trusted.json")
            rx_trust.add(TrustedPeer.from_identity(tx_id))
            extra = {"identity": rx_id, "trust": rx_trust}
            send_kwargs = {"identity": tx_id, "peer": TrustedPeer.from_identity(rx_id)}

        receiver = Receiver(tmp_path / "received", accept_all, device_name="bench", **extra)
        port = await receiver.start("127.0.0.1", 0)
        try:
            result = await send_file("127.0.0.1", port, source, sender_name="bench", **send_kwargs)
        finally:
            await receiver.stop()
        if not result.ok:
            print(f"FAILED: {result.reason}")
            return 1
        saved = tmp_path / "received" / result.saved_as
        same_size = saved.stat().st_size == source.stat().st_size
        mode = "TLS 1.3 encrypted" if secure else "unencrypted"
        print(f"Sent {human_size(result.size)} in {result.seconds:.2f}s "
              f"= {result.mb_per_second:.0f} MB/s (localhost, {mode})")
        print(f"SHA-256 verified by receiver: yes | size matches: {same_size}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-mb", type=int, default=256)
    parser.add_argument("--secure", action="store_true", help="benchmark the encrypted mode")
    args = parser.parse_args()
    return asyncio.run(run(args.size_mb, args.secure))


if __name__ == "__main__":
    raise SystemExit(main())
