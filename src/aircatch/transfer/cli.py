"""Command-line tool for the transfer engine (identity, pairing, secure send/receive).

  python transfer_cli.py identity
  python transfer_cli.py receive --pair            # show a pairing string, then wait for files
  python transfer_cli.py pair-join "aircatch://pair?..."
  python transfer_cli.py peers
  python transfer_cli.py send --to "Laptop" --host 192.168.1.23 myfile.zip
  python transfer_cli.py unpair "Laptop"

Use --config-dir to run several 'devices' on one PC (each folder is a separate device).
Add --insecure to send/receive for the old unencrypted mode (testing only).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from aircatch.pairing.joiner import pair_with
from aircatch.pairing.payload import PairingPayload, PayloadError
from aircatch.pairing.session import PairRequest
from aircatch.security.identity import DeviceIdentity, load_or_create, short_fingerprint
from aircatch.security.trust import TrustedPeer, TrustStore
from aircatch.transfer.events import TransferEvent
from aircatch.transfer.receiver import IncomingOffer, Receiver
from aircatch.transfer.sender import send_file
from aircatch.transfer.util import human_size, local_ip

HINTS = {
    "not_paired": "This device is not paired with the receiver (or was unpaired). Pair again.",
    "tls_error": "Secure connection refused. The devices are probably not paired with each other.",
    "untrusted_peer": "The device at that address is not the one you paired with. Check the address.",
    "could_not_connect": "Nobody answered. Check the address/port, the receiver, and the firewall.",
    "declined": "The receiver said no.",
    "bad_code": "Wrong or already used pairing string.",
    "pairing_closed": "The pairing window is closed or expired. Start it again on the other device.",
    "fingerprint_mismatch": "The device at that address is not the one that made the pairing string.",
}


def default_config_dir() -> Path:
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir("AirCatch", appauthor=False))
    except ImportError:
        return Path.home() / ".aircatch"


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


def load_device(args: argparse.Namespace) -> tuple[DeviceIdentity, TrustStore]:
    config = Path(args.config_dir) if args.config_dir else default_config_dir()
    identity = load_or_create(config, args.name or None)
    return identity, TrustStore(config / "trusted_peers.json")


def show_qr(text: str) -> None:
    try:
        import qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


async def ask(prompt: str) -> bool:
    answer = await asyncio.to_thread(input, prompt)
    return answer.strip().lower() in ("y", "yes")


def cmd_identity(args) -> int:
    identity, trust = load_device(args)
    print(f"Device name : {identity.name}")
    print(f"Device ID   : {identity.fingerprint}")
    print(f"Short ID    : {short_fingerprint(identity.fingerprint)}")
    print(f"Paired with : {len(trust.peers())} device(s)")
    return 0


def cmd_peers(args) -> int:
    _, trust = load_device(args)
    peers = trust.peers()
    if not peers:
        print("No paired devices yet.")
    for p in peers:
        print(f"{p.name:30} {short_fingerprint(p.device_id)}   paired {p.paired_at}")
    return 0


def cmd_unpair(args) -> int:
    _, trust = load_device(args)
    matches = trust.find(args.query)
    if len(matches) != 1:
        print("No match." if not matches else "More than one match; use more of the device ID.")
        return 1
    trust.remove(matches[0].device_id)
    print(f"Unpaired '{matches[0].name}'. (Do the same on the other device.)")
    return 0


async def cmd_receive(args) -> int:
    identity, trust = load_device(args)
    secure = not args.insecure

    async def decide(offer: IncomingOffer) -> bool:
        who = f"{offer.sender_name} (verified)" if offer.verified else f"{offer.sender_name} (UNVERIFIED)"
        print(f"\nIncoming: '{offer.filename}' ({human_size(offer.size)}) from {who}")
        return True if args.auto_accept else await ask("Accept? [y/N] ")

    receiver = Receiver(args.dir, decide, device_name=identity.name, on_event=print_event,
                        max_file_size=args.max_size_mb * 1024 * 1024,
                        identity=identity if secure else None, trust=trust if secure else None)
    port = await receiver.start("0.0.0.0", args.port)
    print(f"AirCatch receiver '{identity.name}' listening on port {port} "
          f"({'encrypted, paired devices only' if secure else 'UNENCRYPTED test mode'})")
    print(f"  this PC's address: {local_ip()}   saving to: {args.dir}")
    print("  (Windows may ask to allow network access: choose Private networks.)")
    if args.pair:
        if not secure:
            print("Pairing needs secure mode.")
            return 1

        async def decide_pair(request: PairRequest) -> bool:
            print(f"\nPairing request from '{request.device_name}' "
                  f"[{short_fingerprint(request.device_id)}] at {request.peer_address}")
            return True if args.auto_accept else await ask("Pair with this device? [y/N] ")

        payload = receiver.open_pairing(local_ip(), decide=decide_pair)
        uri = payload.to_uri()
        print("\nPAIRING OPEN for 2 minutes, one use. On the other device run:")
        print(f'  python transfer_cli.py pair-join "{uri}"')
        show_qr(uri)
    print("  Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        await receiver.stop()
    return 0


async def cmd_pair_join(args) -> int:
    identity, trust = load_device(args)
    try:
        payload = PairingPayload.parse(args.uri)
    except PayloadError as exc:
        print(f"That is not a valid pairing string: {exc}")
        return 1
    print(f"Pairing with '{payload.name}' at {payload.host}:{payload.port} ...")
    print("  (the other device may ask its user to approve)")
    result = await pair_with(payload, identity, trust)
    if result.ok:
        print(f"Paired with '{result.peer.name}' [{short_fingerprint(result.peer.device_id)}]. "
              f"You can now send files both ways.")
        return 0
    print(f"Pairing failed: {result.reason}. {HINTS.get(result.reason, '')}")
    return 1


async def cmd_send(args) -> int:
    identity, trust = load_device(args)
    peer: TrustedPeer | None = None
    if not args.insecure:
        if not args.to:
            print("Say who to send to with --to NAME (see: peers), or use --insecure for testing.")
            return 1
        matches = trust.find(args.to)
        if len(matches) != 1:
            print(f"No single paired device matches '{args.to}'. Paired devices:")
            cmd_peers(args)
            return 1
        peer = matches[0]
    result = await send_file(args.host, args.port, args.file, sender_name=identity.name,
                             on_event=print_event, identity=identity if peer else None, peer=peer)
    print()
    if result.ok:
        print(f"Done: {human_size(result.size)} in {result.seconds:.2f}s "
              f"({result.mb_per_second:.1f} MB/s), saved as '{result.saved_as}'")
        return 0
    print(f"Transfer failed: {result.reason}. {HINTS.get(result.reason, '')}")
    return 1


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-dir", help="folder for this device's identity and paired list")
    common.add_argument("--name", help="device name (saved after first use)")

    parser = argparse.ArgumentParser(prog="transfer_cli.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("identity", parents=[common], help="show this device's name and ID")
    sub.add_parser("peers", parents=[common], help="list paired devices")
    u = sub.add_parser("unpair", parents=[common], help="forget a paired device")
    u.add_argument("query", help="device name or start of its ID")
    r = sub.add_parser("receive", parents=[common], help="wait for incoming files")
    r.add_argument("--dir", default="received")
    r.add_argument("--port", type=int, default=5001)
    r.add_argument("--max-size-mb", type=int, default=10 * 1024)
    r.add_argument("--auto-accept", action="store_true", help="accept without asking (testing)")
    r.add_argument("--pair", action="store_true", help="open a 2-minute pairing window")
    r.add_argument("--insecure", action="store_true", help="no encryption (testing only)")
    j = sub.add_parser("pair-join", parents=[common], help="pair using a pairing string")
    j.add_argument("uri")
    s = sub.add_parser("send", parents=[common], help="send one file")
    s.add_argument("file")
    s.add_argument("--host", required=True)
    s.add_argument("--port", type=int, default=5001)
    s.add_argument("--to", help="name (or ID start) of the paired receiver")
    s.add_argument("--insecure", action="store_true", help="no encryption (testing only)")
    args = parser.parse_args()
    handlers = {"identity": cmd_identity, "peers": cmd_peers, "unpair": cmd_unpair,
                "receive": cmd_receive, "pair-join": cmd_pair_join, "send": cmd_send}
    handler = handlers[args.command]
    try:
        if asyncio.iscoroutinefunction(handler):
            return asyncio.run(handler(args))
        return handler(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
