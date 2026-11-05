# AirCatch

Gesture-controlled wireless file transfer. Milestone 1: app shell + webcam preview.

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
