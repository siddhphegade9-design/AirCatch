# AirCatch Milestone 6: the app, the animations, and the wiring

Milestone 6 turns the engines from Milestones 1-5 into one working desktop app. You can pair two
devices, choose a file, grab and throw it with your hand (or press a button), see the real picture
pop up on the other device, catch it, and get a checked file.

## What happens, step by step

| Moment | Sender's screen | Receiver's screen | Real event behind it |
|---|---|---|---|
| File chosen | The file card pops in with its real thumbnail | | `chooseFile` |
| Grab (fist) | The card **zooms out** to half size and follows your hand | | gesture `grab` |
| Throw (flick) | The card shrinks and **flies off** in your throw direction | | gesture `throw` starts a real `send_file` |
| Offer arrives | "Waiting for X to catch..." | The thumbnail **rises from the bottom to the middle**, floats and glows | receiver `offer` |
| Catch | | A small pulse, then a progress ring fills | gesture `catch` (or the Catch button) |
| Bytes move | Progress bar | Progress ring | real `progress` events |
| Verified | "Delivered" | The file **zooms in** with a green check, Open / Show folder / Done | receiver `completed` (saved AND SHA-256 matched) |
| Failure | The card comes back with a red flash and the reason | Red shake, then close | `failed` / `rejected` |

Rules this follows:
- A throw with no file, no paired device, or an unreachable device **plays no flight** and sends nothing.
  It tells you why.
- Nothing is sent until the receiver accepts. The zoom-in plays only after the hash is verified.
- When an offer arrives the app switches itself to RECEIVE mode so your catch gesture works, and
  switches back afterwards.
- Everything also works with buttons (Send, Catch, Decline), so a demo never depends on the camera.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

### Two instances on one PC (the one-computer demo)

Open two terminals in the project folder (with `(.venv)` active):

```powershell
python run.py --config-dir devices\A --name Laptop-A --download-dir devices\A\inbox --port 5001
python run.py --config-dir devices\B --name Laptop-B --download-dir devices\B\inbox --port 5002
```

1. In window A click **Pair a device**. In window B click **Join with code**, paste the code
   (use **Copy code** in A, **Paste** in B), click **Join**.
2. Approve the pairing in A ("Allow").
3. In B: drop an image onto the window (or **Choose file**), pick Laptop-A in the device list, and
   press **Send** (this is a throw without a camera).
4. A: the picture rises into the middle. Press **Catch**. The file zooms in; **Show folder** opens `devices\A\inbox`.

On one PC, devices on the same machine may not appear in each other's list (the discovery test is for
two laptops). If Laptop-A shows as OFFLINE in B, type `127.0.0.1:5001` into the **Address** box in
B's Devices card. That is the app's built-in manual fallback.

### Options

| Option | Meaning |
|---|---|
| `--config-dir` | Where this device keeps its identity and paired devices |
| `--download-dir` | Where received files go (default `Downloads\AirCatch`) |
| `--name` | Device name shown to others |
| `--port` | Listening port (default: any free port) |
| `--interface` | Local IP of the adapter to use for discovery (the Windows fix from Milestone 4) |
| `--no-advertise` | Do not announce this device on the network |
| `--camera N` | Camera number (find it with `python gesture_demo.py --list-cameras`) |

The same settings can be given as `AIRCATCH_CONFIG_DIR`, `AIRCATCH_DOWNLOAD_DIR`, `AIRCATCH_NAME`,
`AIRCATCH_PORT`, `AIRCATCH_INTERFACE`.

## How it is built

```
camera -> VisionWorker (thread) -> frames + EngineStatus + gesture events
                                         |
QML screens  <----- AppController ------+------> NetworkService (thread, asyncio)
 (senderPhase / receivePhase drive              receiver, discovery, pairing, send_file
  every animation)                              reports real TransferEvents back
```

| File | Job |
|---|---|
| `service.py` | Network service. No Qt. Receiver, discovery, advertising, pairing, sending, all on one thread |
| `ui/vision_worker.py` | Camera + MediaPipe + gesture engine on a worker thread, so the window never stutters |
| `ui/controller.py` | Decides what gestures and buttons mean. Owns the animation phases |
| `ui/thumbnails.py` | Picture previews (sender) and bounded decoding of incoming previews (receiver) |
| `ui/main.qml` | The screens and all animations |
| `transfer/protocol.py` | `encode_thumbnail` / `decode_thumbnail` (see `docs/protocol.md`) |

The gesture engine, tracker and transfer engine were **not** changed in how they work. The only change to
the transfer engine is the optional preview field and `Receiver.allow_peer`, which lets a device that has
just paired (as the joining side) receive files right away.

## Safety

- Received files are never launched automatically. **Open** refuses program types (`.exe`, `.bat`,
  `.ps1`, `.js`, `.lnk` and similar) and shows the folder instead.
- The preview image from another device is size-checked, decoded with a bounded reader, and ignored
  if anything is off. A bad preview never rejects or breaks a transfer.
- Only one incoming file at a time; a second one is declined automatically.
- Unpairing asks twice ("Unpair", then "Sure?").

## Tests

```powershell
python -m pytest
```

229 tests. The new ones cover: the network service with two real devices (pairing, both directions,
decline, timeout, unpair, shutdown), the controller (every gesture and button path, including that
a throw never fakes a send), the camera thread (with fake cameras and scripted hands), previews and
their limits, the real `main.qml` loading with every state and no QML warnings, and two complete app
brains pairing, sending, catching and verifying over loopback.

## What is NOT verified (please test these on your PC)

- **Animation feel on a real screen.** I checked the frames in a headless render. Smoothness on your GPU
  and monitor is yours to judge. Turn on **Reduce motion** if anything feels heavy.
- **A real hand and camera.** The wiring is tested with scripted hands. How well your hand is seen is the
  Milestone 5 trial.
- **Two physical laptops on Wi-Fi.** All tests use loopback.
- **The native file chooser and drag-and-drop** on Windows.
- **Hand-following of the card** (the card tracks your palm while you hold the fist). The maths is tested;
  how it looks is not.

## Known limitations

- One transfer at a time in each direction. No resume after an interruption.
- The thumbnail covers pictures (PNG, JPG, GIF, BMP, WebP). Other files show a coloured type badge.
- The throw direction affects only which way the card flies on the sender's screen, not anything on the
  receiver's side.
- The flight and the pop-up are two separate animations on two separate screens, each triggered by its own
  real event. They are not one continuous object travelling between the windows.
