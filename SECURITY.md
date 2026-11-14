# Security

## Reporting a problem

If you find a security problem in AirCatch, please **do not open a public issue**. Report it
privately through GitHub: on this repository open the **Security** tab and choose **Report a
vulnerability**. Please include what you did, what happened, and the AirCatch version (shown at the
bottom of the window, or `AirCatch.exe --version`).

AirCatch is a student project maintained by one person in spare time. Reports are read and taken
seriously, but there is no guaranteed response time and no bug bounty.

## Supported versions

Only the latest release gets fixes.

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| older development builds | No |

## What AirCatch protects against

| Threat | Protection |
|---|---|
| Someone on the same Wi-Fi reading your files | Everything is encrypted with TLS 1.3. |
| An unknown device sending you files | Only paired devices can. Each device has its own certificate, and a device is trusted by that exact certificate (checked on both sides of every connection). |
| Someone pretending to be your paired device | They would need that device's private key, which never leaves it. A certificate merely *signed by* a paired device is refused. |
| Pairing with the wrong device | Pairing needs a one-time 128-bit code shown on the other PC, valid for 2 minutes, for one device, with 3 attempts. The joining PC checks the host's certificate against the code before sending anything. |
| A damaged or tampered file | Nothing is saved until the whole file matches its SHA-256. |
| A file name that tries to escape your Downloads folder or disguise a program | Names are cleaned: no folders, no reserved Windows names, no invisible characters. Existing files are never overwritten. |
| Running a received program by accident | AirCatch never launches programs, and marks received files as coming from another computer so that Windows warns first. |
| One machine blocking everyone else | Limits on connections per machine and on how long an unpaired device may stay connected. |

## What it does not protect against

Be aware of these before relying on AirCatch for anything sensitive:

- **No independent audit.** The design was reviewed and attacked only by its author, with automated
  scanners and tests ([docs/milestone-10.md](docs/milestone-10.md)).
- **The installer is not code-signed.** Windows shows a SmartScreen warning, and you cannot verify
  the publisher. Compare the download's SHA-256 with `SHA256SUMS.txt` from the release page.
- **The private key is not encrypted on disk.** It is stored in your Windows user profile
  (`%LOCALAPPDATA%\AirCatch\identity\device.key`). Anyone who can read your profile, or malware
  running as you, can copy it and impersonate this device to your paired devices.
- **A paired device is fully trusted to send you files.** You still accept each file, but if a paired
  PC is compromised, treat what it sends with care.
- **Anyone on the network can see that AirCatch is running** (device name and address are announced
  so that devices can find each other) and can try to connect; they cannot send files without pairing.
- **File names appear in the log** (`%LOCALAPPDATA%\AirCatch\aircatch.log`). Think before sharing logs.
- **The camera picture is processed on your PC only** and is never stored or sent; AirCatch has no
  server and makes no internet connections. The libraries it is built from (Qt, MediaPipe, OpenCV)
  are trusted to behave the same way; that was not separately verified.

## More detail

- [docs/security.md](docs/security.md): how identity, pairing and trust work.
- [docs/protocol.md](docs/protocol.md): what is sent over the network.
- [docs/milestone-10.md](docs/milestone-10.md): the self-audit, with everything found and fixed.
