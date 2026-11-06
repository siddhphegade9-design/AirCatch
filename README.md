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

## Milestone 2: transfer engine
Benchmark (localhost, measures CPU/disk, not Wi-Fi):

    python -m aircatch.transfer.benchmark --size-mb 512

Real transfer between two terminals or two devices:

    python -m aircatch.transfer.cli receive --dir received --port 5001
    python -m aircatch.transfer.cli send --host 127.0.0.1 --port 5001 somefile.zip

Protocol description: `docs/protocol.md`
