# AirCatch security notes (Milestone 3)

## What is protected
| Threat | Defence |
|---|---|
| Someone on the Wi-Fi reads your files | All traffic is TLS 1.3 encrypted (modern ciphers, forward secrecy). |
| Someone sends you files without being paired | The receiver only talks to certificates in its paired list, and checks that list again for every offer. Unknown devices can only ask to pair. |
| Someone pretends to be your receiver | The sender accepts only the exact certificate it paired with (pinning). |
| Man-in-the-middle during pairing | The joiner checks the host's certificate fingerprint against the QR code. The proof of the one-time token is bound to both certificates. |
| Guessing the pairing code | The token is random, 128 bits. The window lasts 2 minutes, works once, and closes after 3 wrong tries. |
| A pairing string being reused | Single use: it is invalid after the first successful pairing. |
| A paired device lying about its name | The receiver shows the name stored at pairing time, never the name in the offer. |
| Unpaired device still sending | Unpairing is immediate, even from another program, because the trusted list is re-read when it changes. |
| Flooding the receiver | Connection limit, TLS handshake timeout, transfer limits, per-pairing attempt limit. |
| Tampered or corrupted files | SHA-256 check before anything is saved (unchanged from Milestone 2). |

## What is NOT protected (be honest about these)
- **The pairing string is a secret for 2 minutes.** Anyone who gets it before you use it could start pairing. The host's approval prompt is the safety net: only approve a name you recognise.
- **Private key on disk is not encrypted.** It lives in the config folder (`%LOCALAPPDATA%\AirCatch\identity\device.key` by default) and is protected only by your Windows user account. Anyone who can read your files can copy it. A later milestone will move it to Windows Credential Manager or DPAPI.
- **A paired device is trusted to send you files** (you still approve each one). Unpair devices you no longer use, on both sides.
- **Unpair on both devices.** Unpairing on one side stops that side accepting files, and the other side's sends will be refused. The other device still lists you until you remove it there too.
- **Certificates last 10 years** and there is no key rotation yet.
- **IP address and port are visible** on the network (but not file names or contents).
- A device that is already paired **and** compromised can do whatever a paired device can do.
- This has not had an independent security audit. It uses standard components (TLS 1.3 from OpenSSL via Python, the `cryptography` library, HMAC-SHA256) and no home-made cryptography.

## Never commit these
`device.key` and `trusted_peers.json`. Keep test devices under `devices/` and keep that folder in `.gitignore`.
