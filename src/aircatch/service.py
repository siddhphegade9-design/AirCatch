"""Network service for the desktop app. It knows nothing about Qt, windows or gestures.

One background thread runs one asyncio loop that owns the receiver, discovery, advertising,
pairing and outgoing transfers. The app talks to it through a few thread-safe methods and
hears back through a single `emit(kind, payload)` callback. Event kinds:

  status           {text, ready, port, name}
  devices          {items: [{device_id, name, paired, online, address, status}]}
  offer            {transfer_id, sender, filename, size, thumbnail, verified}
  offer_closed     {transfer_id, accepted}          (answered, timed out, or sender went away)
  transfer         {event: TransferEvent}           (real progress / completion, both directions)
  send_result      {ok, reason, text, filename, saved_as, seconds, mb_s}
  pairing_request  {name, address, device_id}       (someone scanned our code and wants in)
  pairing_closed   {}
  paired           {name}                           (someone paired with us)
  join_result      {ok, text}                       (the result of us joining someone else)
  send_step        {text}                           ("Looking for X...", "Connecting to ...")
  connection_result {ok, text}                      (answer to "Test connection")
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import ssl
import threading
from pathlib import Path
from typing import Callable

from aircatch.discovery.browser import Discovery
from aircatch.discovery.connect import send_to_discovered
from aircatch.discovery.net import lan_ipv4_addresses
from aircatch.discovery.service import Advertiser
from aircatch.pairing.joiner import pair_with
from aircatch.pairing.payload import PairingPayload, PayloadError
from aircatch.security import tls
from aircatch.security.identity import DeviceIdentity, load_or_create
from aircatch.security.trust import TrustedPeer, TrustStore
from aircatch.transfer.events import TransferEvent
from aircatch.transfer.receiver import DEFAULT_MAX_FILE_SIZE, IncomingOffer, Receiver
from aircatch.transfer.sender import send_file
from aircatch.transfer.util import local_ip

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], None]

REASONS = {
    "declined": "The other device said no.",
    "not_found": "Could not find that device on the network, and no saved address worked. Open "
                 "AirCatch there on the same Wi-Fi, or type its address. Use Test connection to check.",
    "could_not_connect": "Could not connect to the other PC. Check that AirCatch is open there, that "
                         "both PCs are on the same network, and that Windows Firewall allows AirCatch "
                         "on Private networks. Use Test connection to find out why.",
    "not_paired": "The devices are not paired (or one side unpaired). Pair them again.",
    "untrusted_peer": "That device is not the one you paired with. Nothing was sent.",
    "too_large": "The file is bigger than the other device allows.",
    "disk_full": "The other device does not have enough free space.",
    "busy": "The other device is busy. Try again in a moment.",
    "duplicate": "That transfer is already in progress.",
    "connection_lost": "The connection was lost.",
    "timeout": "The other device stopped responding.",
    "hash_mismatch": "The file arrived damaged and was discarded.",
    "file_not_found": "The file no longer exists.",
    "file_unreadable": "The file could not be read.",
    "file_changed": "The file changed while it was being sent.",
    "bad_address": "That address is not valid. Use something like 192.168.1.23:5001.",
    "cancelled": "Cancelled.",
    "bad_code": "Wrong or already used pairing code.",
    "pairing_closed": "The pairing window is closed or expired. Open it again on the other device.",
    "fingerprint_mismatch": "The device at that address is not the one that made the code.",
    "tls_error": "The secure connection failed.",
}


def reason_text(reason: str) -> str:
    return REASONS.get(reason, f"Failed ({reason}).")


_HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")


class KnownAddresses:
    """Where each paired device was last reached: device_id -> (address, port).

    A fallback for networks where discovery (mDNS) does not work. It is learned when two devices
    pair and whenever discovery sees a device, and it is only ever a hint: the connection is still
    pinned to the paired certificate, so a wrong or stale address can only fail, never mislead.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, tuple[str, int]] = {}
        try:
            raw = json.loads(path.read_text("utf-8"))
            for device_id, entry in raw.items():
                host, port = entry["host"], entry["port"]
                if isinstance(host, str) and _HOST.match(host) and isinstance(port, int) \
                        and not isinstance(port, bool) and 1 <= port <= 65535:
                    self._data[str(device_id)] = (host, port)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._data = {}

    def get(self, device_id: str) -> tuple[str, int] | None:
        return self._data.get(device_id)

    def set(self, device_id: str, host: str, port: int) -> bool:
        if not (_HOST.match(host) and 1 <= port <= 65535) or self._data.get(device_id) == (host, port):
            return False
        self._data[device_id] = (host, port)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: {"host": h, "port": p} for k, (h, p) in self._data.items()}),
                           "utf-8")
            os.replace(tmp, self._path)
        except OSError:
            log.warning("could not save known addresses", exc_info=True)
        return True

    def forget(self, device_id: str) -> None:
        if self._data.pop(device_id, None) is not None:
            with contextlib.suppress(OSError):
                self._path.write_text(json.dumps({k: {"host": h, "port": p}
                                                  for k, (h, p) in self._data.items()}), "utf-8")


def lan_addresses_preferred_first() -> list[str]:
    """This PC's LAN addresses, with the one that really carries network traffic first.

    A laptop can have several (Wi-Fi plus virtual adapters from WSL, Hyper-V, VirtualBox or a VPN).
    Other devices must be told the Wi-Fi one, so we put the address Windows uses for outgoing
    traffic ahead of the rest instead of trusting alphabetical order.
    """
    addresses = lan_ipv4_addresses()
    preferred = local_ip()
    return sorted(addresses, key=lambda a: a != preferred)


def parse_address(text: str) -> tuple[str, int] | None:
    """'192.168.1.23:5001' -> ('192.168.1.23', 5001), or None when it does not look right."""
    host, sep, port_text = text.strip().rpartition(":")
    if not sep or not _HOST.match(host) or not port_text.isdigit():
        return None
    port = int(port_text)
    return (host, port) if 1 <= port <= 65535 else None


class NetworkService(threading.Thread):
    def __init__(self, emit: Emit, *, config_dir: str | Path, download_dir: str | Path,
                 name: str | None = None, port: int = 0, advertise: bool = True,
                 interface: str | None = None, decide_timeout: float = 60.0,
                 find_timeout: float = 8.0, max_file_size: int = DEFAULT_MAX_FILE_SIZE) -> None:
        super().__init__(name="AirCatchNetwork", daemon=True)
        self._emit_cb = emit
        self._config_dir = Path(config_dir)
        self._download_dir = Path(download_dir)
        self._wanted_name = name
        self._port = port
        self._advertise = advertise
        self._interface = interface
        self._decide_timeout = decide_timeout
        self._find_timeout = find_timeout
        self._max_file_size = max_file_size

        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()
        self.start_error = ""
        self.identity: DeviceIdentity | None = None
        self.trust: TrustStore | None = None
        self.receiver: Receiver | None = None
        self.discovery: Discovery | None = None
        self.advertiser: Advertiser | None = None
        self.known: KnownAddresses | None = None
        self.port: int = 0
        self._stop_event: asyncio.Event | None = None
        self._offers: dict[str, asyncio.Future] = {}
        self._pairing_fut: asyncio.Future | None = None
        self._send_task: asyncio.Task | None = None
        self._last_devices: list[dict] | None = None

    # ------------------------------------------------------------------ helpers
    def _emit(self, kind: str, payload: dict | None = None) -> None:
        with contextlib.suppress(Exception):     # a buggy listener must never break networking
            self._emit_cb(kind, payload or {})

    @property
    def device_name(self) -> str:
        return self.identity.name if self.identity else (self._wanted_name or "AirCatch")

    def _schedule(self, coro) -> "asyncio.Future | None":
        loop = self.loop
        if (loop is None or not self.ready.is_set() or self.start_error
                or loop.is_closed() or not loop.is_running()):
            coro.close()
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:                     # the loop closed between the check and now
            coro.close()
            return None

    def _call_soon(self, fn, *args) -> None:
        loop = self.loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(fn, *args)

    # ------------------------------------------------------------------ thread body
    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:                 # last resort: report, never crash the app
            log.exception("network service crashed")
            self.start_error = self.start_error or str(exc)
            self._emit("status", {"text": f"Network service unavailable: {self.start_error}",
                                  "ready": False, "port": 0, "name": self.device_name})
        finally:
            self.ready.set()

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self.identity = load_or_create(self._config_dir, self._wanted_name)
        self.trust = TrustStore(self._config_dir / "trusted_peers.json")
        self.known = KnownAddresses(self._config_dir / "known_addresses.json")
        azc = None
        try:
            self.receiver = Receiver(
                self._download_dir, self._decide_offer, device_name=self.identity.name,
                on_event=self._on_transfer_event, max_file_size=self._max_file_size,
                identity=self.identity, trust=self.trust, decide_timeout=self._decide_timeout)
            try:
                self.port = await self.receiver.start("0.0.0.0", self._port)
            except OSError as exc:
                self.start_error = f"could not listen on port {self._port or 'auto'}: {exc}"
                self._emit("status", {"text": f"Network service unavailable: {self.start_error}",
                                      "ready": False, "port": 0, "name": self.identity.name})
                self.ready.set()
                return

            if self._interface:
                from zeroconf.asyncio import AsyncZeroconf
                azc = AsyncZeroconf(interfaces=[self._interface])
            advertised = False
            addresses = lan_addresses_preferred_first()
            if self._advertise and addresses:
                try:
                    self.advertiser = Advertiser(self.identity, self.port, addresses=addresses,
                                                 zeroconf=azc)
                    await self.advertiser.start()
                    advertised = True
                except Exception:
                    log.warning("could not advertise on the network", exc_info=True)
                    self.advertiser = None
            self.discovery = Discovery(self.trust, own_identity=self.identity, zeroconf=azc,
                                       on_change=self._publish_devices)
            try:
                await self.discovery.start()
            except Exception:
                log.warning("could not start discovery", exc_info=True)
            text = (f"Visible on this network · port {self.port}" if advertised
                    else f"Listening on port {self.port} (not visible to others)")
            log.info("service ready: %s as '%s' addresses=%s advertised=%s", text, self.identity.name,
                     addresses, advertised)
            self._emit("status", {"text": text, "ready": True, "port": self.port,
                                  "name": self.identity.name})
            self._publish_devices()
            self.ready.set()
            publisher = asyncio.create_task(self._publisher())
            try:
                await self._stop_event.wait()
            finally:
                publisher.cancel()
        finally:
            await self._shutdown(azc)

    async def _shutdown(self, azc) -> None:
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
            with contextlib.suppress(BaseException):
                await self._send_task
        for step in (self.discovery, self.advertiser, self.receiver):
            if step is not None:
                with contextlib.suppress(Exception):
                    await step.stop()            # the advertiser also says goodbye here
        if azc is not None:
            with contextlib.suppress(Exception):
                await azc.async_close()

    async def _publisher(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            self._publish_devices()

    def stop(self, timeout: float = 6.0) -> None:
        """Stop everything cleanly. Safe to call more than once."""
        stop_event = self._stop_event
        if stop_event is not None:
            self._call_soon(stop_event.set)
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    # ------------------------------------------------------------------ devices
    def _publish_devices(self) -> None:
        if self.trust is None or self.discovery is None:
            return
        found = self.discovery.devices()
        online = {d.peer.device_id: d for d in found if d.peer is not None}
        items: list[dict] = []
        for peer in self.trust.peers():
            device = online.get(peer.device_id)
            if device is not None and device.addresses and self.known is not None:
                self.known.set(peer.device_id, device.addresses[0], device.port)
            items.append({"device_id": peer.device_id, "name": peer.name, "paired": True,
                          "online": device is not None,
                          "address": device.addresses[0] if device and device.addresses else "",
                          "status": "ONLINE" if device else "OFFLINE"})
        for device in found:
            if device.peer is None:
                items.append({"device_id": "", "name": device.name, "paired": False, "online": True,
                              "address": device.addresses[0] if device.addresses else "",
                              "status": "NOT PAIRED"})
        if items != self._last_devices:
            log.info("devices: %s", [(i["name"], i["status"], i["address"]) for i in items])
            self._last_devices = items
            self._emit("devices", {"items": items})

    def unpair(self, device_id: str) -> bool:
        if self.trust is None:
            return False
        removed = self.trust.remove(device_id)
        if self.known is not None:
            self.known.forget(device_id)
        log.info("unpaired a device (removed=%s)", removed)
        self._call_soon(self._publish_devices)
        return removed

    # ------------------------------------------------------------------ incoming files
    async def _decide_offer(self, offer: IncomingOffer) -> bool:
        assert self.loop is not None
        future = self.loop.create_future()
        self._offers[offer.transfer_id] = future
        self._emit("offer", {"transfer_id": offer.transfer_id, "sender": offer.sender_name,
                             "filename": offer.filename, "size": offer.size,
                             "thumbnail": offer.thumbnail, "verified": offer.verified})
        accepted = False
        try:
            accepted = await future
            return accepted
        finally:                                 # also runs when the receiver's timeout cancels us
            self._offers.pop(offer.transfer_id, None)
            self._emit("offer_closed", {"transfer_id": offer.transfer_id, "accepted": accepted is True})

    def answer_offer(self, transfer_id: str, accepted: bool, reason: str = "") -> None:
        """Answer an offer. A refusal may carry a reason the sender understands, e.g. "busy"."""
        def resolve() -> None:
            future = self._offers.get(transfer_id)
            if future is not None and not future.done():
                future.set_result(True if accepted else (reason or False))
        self._call_soon(resolve)

    def _on_transfer_event(self, event: TransferEvent) -> None:
        self._emit("transfer", {"event": event})

    # ------------------------------------------------------------------ pairing
    def open_pairing(self) -> str:
        """Open a 2-minute, single-use pairing window. Returns the string for the QR code."""
        future = self._schedule(self._open_pairing())
        if future is None:
            return ""
        try:
            return future.result(3.0)
        except Exception:
            log.warning("could not open pairing", exc_info=True)
            return ""

    async def _open_pairing(self) -> str:
        assert self.receiver is not None
        addresses = lan_addresses_preferred_first()
        host = addresses[0] if addresses else "127.0.0.1"
        payload = self.receiver.open_pairing(host, ttl=120.0, decide=self._decide_pairing,
                                             on_paired=self._on_paired)
        return payload.to_uri()

    def close_pairing(self) -> None:
        if self.receiver is not None:
            self._call_soon(self.receiver.close_pairing)

    async def _decide_pairing(self, request) -> bool:
        assert self.loop is not None
        future = self.loop.create_future()
        self._pairing_fut = future
        self._emit("pairing_request", {"name": request.device_name, "address": request.peer_address,
                                       "device_id": request.device_id})
        try:
            return await future
        finally:
            self._pairing_fut = None
            self._emit("pairing_closed", {})

    def answer_pairing(self, accepted: bool) -> None:
        def resolve() -> None:
            future = self._pairing_fut
            if future is not None and not future.done():
                future.set_result(bool(accepted))
        self._call_soon(resolve)

    def _on_paired(self, peer: TrustedPeer) -> None:
        address = self.receiver.paired_address(peer.device_id) if self.receiver else None
        if address is not None and self.known is not None:
            self.known.set(peer.device_id, address[0], address[1])
        log.info("paired with '%s' (address learned: %s)", peer.name, address)
        self._emit("paired", {"name": peer.name})
        self._publish_devices()

    def join_pairing(self, text: str) -> None:
        """Pair with a device that is showing a pairing code. Result arrives as 'join_result'."""
        if self._schedule(self._join(text)) is None:
            self._emit("join_result", {"ok": False, "text": "The network service is not ready yet."})

    async def _join(self, text: str) -> None:
        assert self.identity is not None and self.trust is not None
        try:
            payload = PairingPayload.parse(text)
        except PayloadError as exc:
            self._emit("join_result", {"ok": False, "text": f"That is not a pairing code: {exc}."})
            return
        log.info("joining a pairing at %s:%s", payload.host, payload.port)
        result = await pair_with(payload, self.identity, self.trust, listen_port=self.port)
        log.info("join result: ok=%s reason=%s", result.ok, result.reason)
        if result.ok and result.peer is not None:
            if self.known is not None:
                self.known.set(result.peer.device_id, payload.host, payload.port)
            if self.receiver is not None:
                self.receiver.allow_peer(result.peer)
            self._emit("join_result", {"ok": True, "text": f"Paired with {result.peer.name}."})
            self._publish_devices()
        else:
            self._emit("join_result", {"ok": False, "text": reason_text(result.reason)})

    # ------------------------------------------------------------------ outgoing files
    def send(self, device_id: str, path: str, thumbnail: bytes | None = None,
             manual_address: str = "") -> None:
        """Start sending. Everything that happens afterwards is reported as events."""
        if self._schedule(self._start_send(device_id, path, thumbnail, manual_address)) is None:
            self._emit("send_result", {"ok": False, "reason": "error", "filename": Path(path).name,
                                       "text": "The network service is not ready yet."})

    def cancel_send(self) -> None:
        def cancel() -> None:
            if self._send_task is not None and not self._send_task.done():
                self._send_task.cancel()
        self._call_soon(cancel)

    def _fail_send(self, filename: str, reason: str) -> None:
        self._emit("send_result", {"ok": False, "reason": reason, "text": reason_text(reason),
                                   "filename": filename})

    async def _start_send(self, device_id: str, path: str, thumbnail: bytes | None,
                          manual: str) -> None:
        assert self.trust is not None
        filename = Path(path).name
        if self._send_task is not None and not self._send_task.done():
            self._fail_send(filename, "busy")
            return
        peer = self.trust.get(device_id) if device_id else None
        if peer is None:
            self._fail_send(filename, "not_paired")
            return
        self._send_task = asyncio.ensure_future(self._do_send(peer, Path(path), thumbnail, manual))

    def _step(self, text: str) -> None:
        log.info("send step: %s", text)
        self._emit("send_step", {"text": text})

    async def _send_to(self, address: tuple[str, int], path: Path, peer: TrustedPeer,
                       thumbnail: bytes | None):
        assert self.identity is not None
        return await send_file(address[0], address[1], path, sender_name=self.identity.name,
                               on_event=self._on_transfer_event, identity=self.identity, peer=peer,
                               thumbnail=thumbnail, connect_timeout=5.0)

    async def _do_send(self, peer: TrustedPeer, path: Path, thumbnail: bytes | None,
                       manual: str) -> None:
        assert self.identity is not None and self.discovery is not None and self.known is not None
        filename = path.name
        log.info("send: '%s' to '%s' (typed address: %s)", filename, peer.name, manual.strip() or "none")
        try:
            known = self.known.get(peer.device_id)
            result = None
            if manual.strip():
                address = parse_address(manual)
                if address is None:
                    self._fail_send(filename, "bad_address")
                    return
                self._step(f"Connecting to {peer.name} at {address[0]}...")
                result = await self._send_to(address, path, peer, thumbnail)
            else:
                self._step(f"Looking for {peer.name} on the network...")
                device = await self.discovery.find(
                    peer, min(2.5, self._find_timeout) if known else self._find_timeout)
                if device is not None:
                    result = await send_to_discovered(
                        device, path, identity=self.identity, sender_name=self.identity.name,
                        on_event=self._on_transfer_event, thumbnail=thumbnail, on_step=self._step)
                if (result is None or result.reason == "could_not_connect") and known:
                    self._step(f"Trying {peer.name} at its last known address {known[0]}...")
                    result = await self._send_to(known, path, peer, thumbnail)
                if result is None:
                    self._fail_send(filename, "not_found")
                    return
        except asyncio.CancelledError:
            self._fail_send(filename, "cancelled")
            raise
        except Exception:
            log.exception("sending failed unexpectedly")
            self._fail_send(filename, "error")
            return
        log.info("send result: ok=%s reason=%s seconds=%.2f", result.ok, result.reason or "-",
                 result.seconds)
        self._emit("send_result", {
            "ok": result.ok, "reason": result.reason,
            "text": "" if result.ok else reason_text(result.reason), "filename": filename,
            "saved_as": result.saved_as, "seconds": result.seconds, "mb_s": result.mb_per_second,
            "receiver": result.receiver_name})

    # ------------------------------------------------------------------ "Test connection"
    def test_connection(self, device_id: str, manual_address: str = "") -> None:
        """Check whether we can reach a paired device. The answer arrives as 'connection_result'."""
        if self._schedule(self._test_connection(device_id, manual_address)) is None:
            self._emit("connection_result", {"ok": False, "text": "The network service is not ready yet."})

    async def _probe(self, peer: TrustedPeer, host: str, port: int) -> tuple[bool, str]:
        assert self.identity is not None
        context = tls.build_client_context(self.identity, peer.cert_pem)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=context, server_hostname="aircatch"), 4.0)
        except ssl.SSLError:
            return False, "answered, but it is not the device you paired with"
        except (OSError, asyncio.TimeoutError):
            return False, "no answer"
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True, ""

    async def _test_connection(self, device_id: str, manual: str) -> None:
        assert self.trust is not None and self.discovery is not None and self.known is not None
        peer = self.trust.get(device_id) if device_id else None
        if peer is None:
            self._emit("connection_result", {"ok": False, "text": "Select a paired device first."})
            return
        candidates: list[tuple[str, int, str]] = []
        if manual.strip():
            address = parse_address(manual)
            if address is None:
                self._emit("connection_result", {"ok": False, "text": reason_text("bad_address")})
                return
            candidates.append((address[0], address[1], "the address you typed"))
        else:
            device = await self.discovery.find(peer, min(2.5, self._find_timeout))
            if device is not None:
                from aircatch.discovery.connect import order_addresses
                candidates += [(a, device.port, "found on the network")
                               for a in order_addresses(device.addresses)]
            known = self.known.get(peer.device_id)
            if known and all((known[0], known[1]) != (h, p) for h, p, _ in candidates):
                candidates.append((known[0], known[1], "its last known address"))
        if not candidates:
            self._emit("connection_result", {"ok": False, "text": (
                f"{peer.name} is not visible on the network and no address is saved. Type its "
                f"address (like 192.168.1.23:5001) in the Address box, or pair again.")})
            return
        tried = []
        for host, port, source in candidates:
            ok, detail = await self._probe(peer, host, port)
            log.info("connection test to '%s' at %s:%s: %s", peer.name, host, port, detail or "ok")
            if ok:
                self.known.set(peer.device_id, host, port)
                self._emit("connection_result", {"ok": True, "text": (
                    f"Connected to {peer.name} at {host}:{port} ({source}). Sending will work.")})
                return
            tried.append(f"{host}:{port} ({detail})")
        self._emit("connection_result", {"ok": False, "text": (
            f"Could not connect to {peer.name}. Tried " + ", ".join(tried) + ". Check that AirCatch is "
            "open there, both PCs are on the same network, and Windows Firewall allows AirCatch on "
            "Private networks.")})
