# AirCatch

Gesture-controlled wireless file transfer: pair two devices, grab a file with your hand, throw it, and catch it on the other laptop.

## Setup (Windows, PowerShell, from this folder)
    py -3.11 -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt

## Run
    python run.py

## Test
    python -m pytest

## Layout
- `src/aircatch/vision`  camera capture (gestures come next)
- `src/aircatch/ui`      QML interface + Python bridge
- Later: `protocol/`, `transport/`, `pairing/`, `discovery/`, `transfer/`, `gestures/`

## Milestone 2 and 3: transfer engine + security
Install packages (once): `pip install -r requirements.txt`

Speed test (localhost, measures CPU/disk, not Wi-Fi):

    python transfer_bench.py --size-mb 512
    python transfer_bench.py --size-mb 512 --secure

Two test devices on one PC (separate folders = separate identities):

    python transfer_cli.py receive --config-dir devices\A --name Laptop-A --dir devices\A\inbox --pair
    python transfer_cli.py pair-join --config-dir devices\B --name Phone-B "<pairing string>"
    python transfer_cli.py send --config-dir devices\B --to Laptop-A --host 127.0.0.1 --port 5001 run.py

Automatic discovery (receiver announces itself; sender finds it):

    python transfer_cli.py discover
    python transfer_cli.py send --to Laptop-A run.py          (no --host needed)
    python transfer_cli.py send --to Laptop-A --host 192.168.1.23 run.py   (manual fallback)

Other commands: `identity`, `peers`, `unpair NAME`. Add `--insecure` to send/receive for the old unencrypted test mode.

Docs: `docs/protocol.md`, `docs/security.md`, `docs/discovery.md`.
Never commit `devices/` or any `device.key` file.

## Milestone 5: gestures
Install the new packages (once, MediaPipe needs a matching OpenCV and an older NumPy):

    pip uninstall -y opencv-python
    pip install -r requirements.txt

Try it with your camera (a hand test window):

    python gesture_demo.py --list-cameras
    python gesture_demo.py                    (send: open palm, fist, flick and open)
    python gesture_demo.py --mode receive     (catch: open palm, fist)
    python gesture_demo.py --calibrate        (teach it how fast your throws are)
    python gesture_demo.py --benchmark 10     (speed numbers)

Docs: `docs/gestures.md`.


## Milestone 6: the app

    python run.py

Two instances on one PC (separate folders = separate devices):

    python run.py --config-dir devices\A --name Laptop-A --download-dir devices\A\inbox --port 5001
    python run.py --config-dir devices\B --name Laptop-B --download-dir devices\B\inbox --port 5002

Pair with **Pair a device** / **Join with code**, drop a file, **Send** (or grab and throw with the
camera), then **Catch** on the other window. Huawei-style animations: the file zooms out and flies away
on the sender, rises from the bottom on the receiver, and zooms in once the file is verified.

Allow AirCatch through the Windows firewall on **Private networks**.
Full guide, safety notes and the list of what is still unverified: `docs/milestone-6.md`.
Testing on two real PCs, step by step: `docs/two-pc-test.md`.

## Milestone 10: production readiness

Security, reliability and performance audit before packaging: what was checked, what was fixed and
what is still to verify on real PCs is in `docs/milestone-10.md`. After updating, run

    pip install -r requirements.txt --upgrade
    python -m pytest
