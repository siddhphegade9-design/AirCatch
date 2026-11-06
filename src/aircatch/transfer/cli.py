"""Command-line tool for testing the transfer engine on its own.

  python -m aircatch.transfer.cli receive --dir received --port 5001
  python -m aircatch.transfer.cli send --host 127.0.0.1 --port 5001 --name Sender myfile.zip
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from aircatch.transfer.events import TransferEvent
from aircatch.transfer.receiver import IncomingOffer, Receiver
from aircatch.transfer.sender import send_file
from aircatch.transfer.util import human_size, local_ip


def print_event(event: TransferEvent) -> None:
    arrow = "->" if event.direction == "send" else "<-"
    if event.kind == "progress":
        pct = 100 * event.bytes_done / event.total if event.total else 100
        print(f"\r  {arrow} {event.filename}: {pct:5.1f}%  "
              f"({human_size(event.bytes_done)} / {human_size(event.total)})", end="", flush=True)
        return
    text = {"offer": "offer", "accepted": "accepted", "rejected": f"REJECTED ({event.message})",
            "verifying": "verifying SHA-256", "completed": "COMPLETED, verified",
            "failed": f"FAILED ({event.message})"}[event.kind]
    print(f"\n[{event.kind}] {event.filename}: {text}" +
          (f"\n  saved to {event.path}" if event.path else ""), flush=True)


async def cmd_receive(args: argparse.Namespace) -> int:
    async def decide(offer: IncomingOffer) -> bool:
        print(f"\nIncoming: '{offer.filename}' ({human_size(offer.size)}) from "
              f"{offer.sender_name} [{offer.peer}]")
        if args.auto_accept:
            return True
        answer = await asyncio.to_thread(input, "Accept? [y/N] ")
        return answer.strip().lower() in ("y", "yes")

    receiver = Receiver(args.dir, decide, device_name=args.name, on_event=print_event,
                        max_file_size=args.max_size_mb * 1024 * 1024)
    port = await receiver.start("0.0.0.0", args.port)
    print(f"AirCatch receiver '{args.name}' listening on port {port}")
    print(f"  this PC's address: {local_ip()}   saving to: {args.dir}")
    print("  (Windows may ask to allow network access: choose Private networks.)")
    print("  Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        await receiver.stop()
    return 0


async def cmd_send(args: argparse.Namespace) -> int:
    result = await send_file(args.host, args.port, args.file, sender_name=args.name,
                             on_event=print_event)
    print()
    if result.ok:
        print(f"Done: {human_size(result.size)} in {result.seconds:.2f}s "
              f"({result.mb_per_second:.1f} MB/s), saved as '{result.saved_as}'")
        return 0
    print(f"Transfer failed: {result.reason}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="aircatch.transfer.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("receive", help="wait for incoming files")
    r.add_argument("--dir", default="received")
    r.add_argument("--port", type=int, default=5001)
    r.add_argument("--name", default="AirCatch-Receiver")
    r.add_argument("--max-size-mb", type=int, default=10 * 1024)
    r.add_argument("--auto-accept", action="store_true")
    s = sub.add_parser("send", help="send one file")
    s.add_argument("file")
    s.add_argument("--host", required=True)
    s.add_argument("--port", type=int, default=5001)
    s.add_argument("--name", default="AirCatch-Sender")
    args = parser.parse_args()
    try:
        return asyncio.run(cmd_receive(args) if args.command == "receive" else cmd_send(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
