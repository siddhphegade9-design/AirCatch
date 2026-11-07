# AirCatch

Gesture-controlled wireless file transfer. Milestone 1: app shell + webcam preview. Milestone 2: transfer engine (no UI yet).

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

Other commands: `identity`, `peers`, `unpair NAME`. Add `--insecure` to send/receive for the old unencrypted test mode.

Docs: `docs/protocol.md` (rules of the conversation), `docs/security.md` (what is and is not protected).
Never commit `devices/` or any `device.key` file.
