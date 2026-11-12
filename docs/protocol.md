# AirCatch protocol, version 1

One TCP connection carries one file (or one pairing). Either device can be sender or receiver.
In **secure mode** (the default) the whole connection runs inside **TLS 1.3**; the messages below
are identical inside and outside TLS.

## Framing
- **Control message** = 4-byte big-endian length, then that many bytes of UTF-8 JSON (an object with a `"type"` field). Max 64 KiB.
- **File data** = raw bytes, no framing, exactly `size` bytes, sent between `accept` and `done`.

## Transport security (secure mode)
- TLS 1.3 only. Each device has an ECDSA P-256 self-signed certificate. **Device ID = SHA-256 of the certificate (DER), 64 lowercase hex characters.**
- Mutual authentication by **certificate pinning**: the sender only accepts the receiver certificate it paired with; the receiver only accepts client certificates of paired devices, and re-checks the paired list for every offer (so unpairing is immediate).
- A device that has no certificate to show (a *new* device) may connect, but may only send `pair_request`.

## Transfer conversation
1. Sender -> `{"type":"offer","version":1,"transfer_id":"<1-64 chars A-Za-z0-9_->","sender_name":"...","filename":"...","size":12345}`
2. Receiver -> `{"type":"accept","receiver_name":"..."}` **or** `{"type":"reject","reason":"..."}`
3. Sender streams exactly `size` raw bytes, hashing them with SHA-256 as it goes.
4. Sender -> `{"type":"done","sha256":"<64 hex chars>"}`
5. Receiver (after comparing hashes and saving) -> `{"type":"result","ok":true,"saved_as":"name.ext"}` **or** `{"type":"result","ok":false,"reason":"..."}`

In secure mode the receiver ignores `sender_name` and shows the name stored for the sender's verified certificate.
Closing the connection at any time cancels the transfer; the receiver deletes its temporary file.

## Optional preview image (added in Milestone 6, still protocol version 1)

An `offer` may carry one extra field, `"thumbnail"`: standard base64 of a small JPEG or PNG
(at most 20 KB before encoding, so the whole control message stays far below the 64 KB limit).
The receiver's screen shows it while the person decides, so they see the real picture rise up
before accepting.

Rules for the receiver, because the field comes from the network:
- it is **optional**: older devices ignore the field, and a missing one just means "show a file-type badge";
- it is **never a reason to reject** an offer: if the value is not valid base64, is too long, is larger
  than 20 KB, or does not start with the JPEG or PNG signature, it is silently dropped;
- the bytes are still untrusted image data, so the UI decodes them with a size-bounded reader
  (at most 1024 pixels on the longest side) and ignores anything that fails;
- it only ever arrives from a paired, certificate-verified device, and nothing is saved from it.

## Pairing conversation
The host opens a **pairing window** (default 120 s, single use, 3 failed attempts) and shows a pairing string / QR code:

    aircatch://pair?v=1&h=<address>&p=<port>&f=<host cert fingerprint>&t=<one-time token>&n=<host name>

1. Joiner connects with TLS **without** a client certificate and checks that the server certificate's fingerprint equals `f` (this authenticates the host; the QR code is the trusted channel).
2. Joiner -> `{"type":"pair_request","version":1,"device_name":"...","cert":"<PEM>","proof":"<hex>"}`
   where `proof = HMAC-SHA256(key = token, message = "aircatch-pair-v1" || host_fp(32 bytes) || joiner_fp(32 bytes))`.
3. Host checks the proof (constant time), asks its user to approve, then -> `{"type":"pair_accept","device_name":"..."}` **or** `{"type":"pair_reject","reason":"..."}`.
4. Both sides store the other's certificate in their trusted-devices list.

## Discovery
Receivers announce `_aircatch._tcp.local.` over mDNS with TXT records `v=1`, `h=<16-hex hint>`, `n=<name>`.
See `docs/discovery.md`. Discovery tells a sender where a paired device is; identity is still proven by the TLS certificate.

## Reason codes
Receiver: `declined`, `too_large`, `disk_full`, `busy`, `duplicate`, `malformed`, `unsupported_version`, `not_paired`,
`hash_mismatch`, `connection_lost`, `timeout`, `disk_error`, `protocol_error`.
Pairing: `pairing_closed`, `bad_code`, `declined`, `busy`, `malformed`.
Sender/joiner side only: `could_not_connect`, `file_not_found`, `file_unreadable`, `file_changed`,
`tls_error`, `untrusted_peer`, `fingerprint_mismatch`, `not_found` (discovery found nothing).

## Receiver safety rules (unchanged from Milestone 2)
- File names are sanitized (no folders, no `<>:"/\|?*`, no reserved Windows names, length limit).
- Data goes to `<dest>/.aircatch-incoming/<transfer_id>.part` and is moved to its final name only after the SHA-256 matches. Existing files are never overwritten.
- Limits: max file size, free-disk check, max concurrent transfers, max connections, TLS handshake and idle timeouts, duplicate `transfer_id` rejected.

## Not yet implemented
Resume after interruption, private-key encryption at rest (see `docs/security.md`).
