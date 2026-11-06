# AirCatch transfer protocol, version 1

One TCP connection carries one file. Either device can be the sender or receiver.
(Milestone 3 wraps this connection in TLS and adds pairing. The messages below do not change.)

## Framing
- **Control message** = 4-byte big-endian length, then that many bytes of UTF-8 JSON (an object with a `"type"` field). Max 64 KiB.
- **File data** = raw bytes, no framing, exactly `size` bytes, sent between `accept` and `done`.

## Conversation
1. Sender -> `{"type":"offer","version":1,"transfer_id":"<1-64 chars A-Za-z0-9_->","sender_name":"...","filename":"...","size":12345}`
2. Receiver -> `{"type":"accept","receiver_name":"..."}` **or** `{"type":"reject","reason":"..."}`
3. Sender streams exactly `size` raw bytes, hashing them with SHA-256 as it goes.
4. Sender -> `{"type":"done","sha256":"<64 hex chars>"}`
5. Receiver (after comparing hashes and saving) -> `{"type":"result","ok":true,"saved_as":"name.ext"}` **or** `{"type":"result","ok":false,"reason":"..."}`

Closing the connection at any time cancels the transfer. The receiver deletes its temporary file.

## Reason codes
`declined`, `too_large`, `disk_full`, `busy`, `duplicate`, `malformed`, `unsupported_version`,
`hash_mismatch`, `connection_lost`, `timeout`, `disk_error`, `protocol_error`
(sender-side only: `could_not_connect`, `file_not_found`, `file_unreadable`, `file_changed`).

## Receiver safety rules
- File name is sanitized (no folders, no `<>:"/\|?*`, no reserved Windows names, length limit).
- Data goes to `<dest>/.aircatch-incoming/<transfer_id>.part`. Only after the SHA-256 matches is it moved to its final name.
- A name is claimed atomically; an existing file is never overwritten (`name (1).ext`, `name (2).ext`, ...).
- Limits: max file size (configurable), free disk space check, max concurrent transfers, idle timeouts, duplicate `transfer_id` rejected.

## Not yet implemented (later milestones)
TLS encryption, device pairing/authentication, discovery, resume after interruption.
Until pairing exists, anyone on the same network who can reach the port can send an offer. The receiver still asks permission, but do not use this on untrusted networks.
