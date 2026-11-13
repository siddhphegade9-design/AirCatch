# AirCatch Milestone 10: production readiness, reliability, performance and security

This is the record of the audit done before packaging AirCatch as an application: what was
checked, what was found, what was fixed, and what is still not proven.

**Read this first.** Everything here was checked by automated tools, by reading the code, and by
tests that run on one computer (real TLS over localhost, simulated cameras and hands). None of it
was run on Windows, on a real webcam or across a real Wi-Fi network. The list at the end says what
still has to be checked by hand on the two PCs.

## Summary

| Area | Result |
|---|---|
| Security scan of the code (bandit) | 0 high, 4 medium, 15 low. All reviewed: none is a real weakness (see below). |
| Known vulnerabilities in dependencies (pip-audit) | **Found and fixed.** The old minimum versions allowed vulnerable releases of `cryptography`, `zeroconf` and `pillow`. Minimums raised; the audit is now clean. |
| Hostile input to the receiver | 4,000 malformed messages (from unpaired and paired devices): no crash, no error, no stray file. |
| Hostile file names | **Two gaps found and fixed** (disguised extensions, `NUL.tar.gz`). 26 hostile names tested. |
| Connection hogging by an unpaired device | **Found and fixed** (per-machine limit, short time limit before pairing). |
| Damaged files on disk | **Three failures found and fixed** (trust list, name file, identity). |
| Network changes while running | **Gap found and fixed** (the app now re-announces itself). |
| Camera hiccups, slow window | **Two weaknesses found and fixed** (dropped frames, picture backlog). |
| Random-action ("monkey") test of the app logic | 24,000 random actions: no exception, no impossible state. |
| Leaks | None: +0.3 MB over 500 transfers; threads and open files constant over 25 start/stop cycles. |
| Performance | 230 MB/s encrypted on localhost, 67 MB of memory for a 1 GB file; gesture engine 0.8 ms per frame. |
| Tests | 710 pass in the audit environment (1 cannot run there: it needs the exact MediaPipe version). On Windows 11 with Python 3.11: 709 passed, 1 skipped, 1 failed on the first run; that failure was a real bug, since fixed. |

## What was found and fixed

### 1. Dependencies with known vulnerabilities
`requirements.txt` gave only minimum versions, and the minimums were old enough to allow releases
with published vulnerabilities. Auditing the oldest allowed versions found 97 advisories in four
packages. `pillow` matters most, because it sits behind image handling.

New minimums: `cryptography>=50.0.0`, `zeroconf>=0.149.16`, `pillow>=13.0.0`, `pytest>=9.0.3`. With
these the audit reports no known vulnerabilities, and Windows builds for Python 3.11 exist for all
of them. **After updating, run `pip install -r requirements.txt --upgrade`.**

The audit is a snapshot (13 November 2026). New advisories appear all the time: run
`pip-audit -r requirements.txt` again before each release.

### 2. File names that lie (receiver)
A file name arriving from another device is never trusted. Two gaps were closed:

* **Disguised extension.** Invisible "right-to-left" characters make `photo<RLO>gpj.exe` display as
  `photoexe.jpg`. All invisible formatting characters are now removed, so the name shows what it is.
* **Windows device names.** `NUL.tar.gz` and `con.backup.txt` are still the devices `NUL` and `CON`
  on Windows, whatever follows the first dot. They are now renamed, as `NUL.txt` already was.

### 3. One machine could block all transfers (receiver)
The receiver allows 8 connections at a time. A device that is not even paired could open 8, say
nothing, and keep each for 15 seconds, over and over. Now one address may hold at most 4
connections, and a device that has not shown a trusted certificate must speak within 5 seconds (it
can only be asking to pair, which happens at once). A test proves a paired device still gets through
while another machine tries to take every slot; the same test fails without the fix.

### 4. Received files were not marked as "from another computer" (Windows)
Windows warns before running a downloaded program, and Office opens downloaded documents in
Protected View, because browsers mark such files. AirCatch now sets the same mark ("Mark of the
Web") on every file it saves. If the disk cannot hold the mark (FAT32, some network drives) the file
is saved without it. The first run of the tests on Windows caught a bug here (the mark was written
with doubled carriage returns); it now writes the exact bytes, and the test reads the mark back on
Windows. Still to check by eye: the file's Properties show the "came from another computer" notice.

### 5. Damaged files stopped the app
* A trust list of the wrong shape (for example `{"peers": 5}`) stopped the network service. It is
  now treated like any other unreadable trust list: set aside, nobody trusted.
* A name file that was not a JSON object did the same. It is now ignored.
* A damaged or mismatched key or certificate left the app permanently "unavailable" with a cryptic
  message. The app now moves the damaged files to `identity/damaged-<date>/`, creates a new identity,
  and tells the user: *"This device's identity was damaged and has been reset. Pair your devices
  again."* The key is now also checked against the certificate at start-up.

### 6. The app did not notice the network changing
Addresses were read once at start-up. Starting AirCatch before Wi-Fi was connected, waking the laptop
on another network, or getting a new address from the router left the device invisible until a
restart. The service now checks every 4 seconds and, when the addresses change, announces itself
again and starts looking afresh.

### 7. Camera
* One failed read used to stop the camera. Webcams drop the odd frame; now only about a second of
  silence counts as "the camera stopped".
* When the window is busy, camera pictures used to queue up behind it (lag, memory). The camera
  thread now skips *showing* pictures the window has not caught up with. Every frame is still
  analysed, so no gesture is lost.

## What was checked and found sound

* **Encryption and identity.** TLS 1.3 only, certificates in both directions. A device is trusted by
  its exact certificate: a test confirms that a certificate *signed by* a paired device is refused.
  The sender checks the receiver's certificate twice (TLS, then the fingerprint).
* **Pairing.** One-time 128-bit code, valid 2 minutes, 3 attempts, single use. The joining device
  checks the host's certificate against the code before sending anything secret.
* **Receiving.** Nothing is saved before the SHA-256 check passes. A sender that lies about the size,
  sends a wrong or missing hash, or vanishes part way leaves no file and no partial file. Offers
  larger than the limit or the free disk space are refused. Existing files are never overwritten.
* **Previews.** Limited to 20 KB, JPEG or PNG only, decoded with a size-bounded reader; a bad preview
  is ignored and never blocks a transfer.
* **Logs.** No codes, keys or pairing strings are logged. Logs rotate (3 files of 500 KB). Uncaught
  errors and hard crashes are written to `aircatch.log` and `crash.log`.
* **Launching.** Received programs are never started by AirCatch (it shows the folder instead).

### The scanner's remaining notes
* 12 x `assert`: used only to state "this cannot be None here". None guards a security decision.
* 3 x "listens on all network interfaces": intended. A file-transfer app must accept connections
  from the local network; every connection is then authenticated.
* 3 x "weak random numbers": only in the hand simulator and the recording script, never for secrets
  (codes and keys use the operating system's secure generator).
* 1 x "SQL injection": a false alarm (a field named `extra`; there is no database).

## Known limits (not fixed)

1. **The private key is not encrypted on disk.** It lives in the user's own settings folder
   (`%LOCALAPPDATA%\AirCatch\identity\device.key`) and is protected only by the Windows account.
   The proper fix is Windows DPAPI. It was not added here because it cannot be tested without
   Windows, and a mistake would lock users out of their pairings. Do it, and test it, on Windows.
2. **Pairing does not prove the joining device owns its certificate's key.** Someone who already
   knows the one-time code could register a certificate that is not theirs. That gives them nothing
   (they still cannot connect without the key), so it is noted, not fixed.
3. **Any device on the same network can connect and ask to pair** while a pairing window is open, and
   can use up to 4 connection slots at any time. The limits above keep paired devices working, but a
   hostile network can still slow things down.
4. **File names are written to the log.** Useful for support; a privacy point if logs are shared.
5. **A hung webcam driver cannot be interrupted.** If the camera never returns from a read, the
   camera thread waits with it; the app stays usable but the camera has to be restarted.
6. **Gestures are tuned on simulated hands** (see `docs/gestures.md`). Real-camera accuracy is unmeasured
   until a recording from `gesture_record.py` is analysed.

## Still to verify by hand, on the two PCs

- [ ] `pip install -r requirements.txt --upgrade`, then `python -m pytest` passes on Windows.
- [ ] Pair, send both ways, cancel a transfer part way, send a 1 GB file.
- [ ] Receive an `.exe`: Windows shows "This file came from another computer" in its Properties.
- [ ] Start AirCatch with Wi-Fi off, then turn Wi-Fi on: the other PC sees it within about 10 seconds.
- [ ] Unplug the webcam while the camera is on: a clear message, and the camera can be started again.
- [ ] Windows Firewall: the first run asks to allow AirCatch on private networks.
- [ ] Run `python gesture_record.py` on each PC and keep the recordings.

## How to repeat the audit

```
pip install bandit ruff pip-audit
bandit -r src
ruff check src --select F,B,E9,ASYNC,PLE
pip-audit -r requirements.txt
python -m pytest                       # includes tests/test_hardening.py and tests/test_monkey.py
python transfer_bench.py --size-mb 1024 --secure
```
