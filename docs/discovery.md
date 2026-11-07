# AirCatch discovery (Milestone 4)

Discovery answers one question: **where on this network is the device I paired with?**
It does not decide who anyone is; TLS certificate pinning does that when we connect.

## How it works
- Standard **mDNS / DNS-SD** (the same technology as Bonjour and Android's NSD), via the `zeroconf` library.
- A receiver that is running announces the service `_aircatch._tcp.local.` with its address(es), port and these TXT records:
  - `v` = `1` (advertisement format version)
  - `h` = **discovery hint**: first 16 hex characters of `SHA-256("aircatch-discovery-v1" || device_id)`
  - `n` = device display name
- A device that wants to send looks for that service and compares each `h` with the hints of the devices it is paired with.
- `send --to NAME` with no `--host` does exactly this. Giving `--host` always skips discovery (manual fallback).

## Privacy
- The device ID is **never** broadcast, only the one-way hint. A stranger on the network learns the hint and the display name.
- The hint is stable, so a stranger could notice "the same AirCatch device is here again". Use `--no-advertise` to hide the device.
- Nothing is advertised unless a secure receiver is running.

## Security
Everything in an advertisement comes from the network and is treated as untrusted:
- Fields are validated strictly (version, hint format, name length and content, port range). Bad advertisements are ignored.
- Only private/local IPv4 addresses are accepted as targets, so a fake advertisement cannot make us connect to a public server.
- At most 100 devices are tracked (flood protection).
- A fake advertisement can at worst point us at the wrong device. The sender then refuses to talk to it, because TLS pins the exact certificate (`untrusted_peer`). No file is sent.
- Names of devices that are not paired are only labels and are not verified.

## Limits (be aware)
- mDNS uses multicast. It does **not** work across different networks or VLANs, and many guest/campus/office Wi-Fi networks block it. Use `--host` there.
- Windows Firewall must allow Python on **Private** networks (a prompt appears the first time). Public network profiles usually block discovery.
- VPN adapters and virtual adapters (VirtualBox, WSL, Hyper-V) can confuse address selection. If a device advertises several addresses, the sender tries them in order.
- Phones: Android's NSD can use the same service type later. Discovery on Android needs a multicast lock; that comes with the Android app.
