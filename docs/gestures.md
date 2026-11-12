# AirCatch gestures (Milestone 5)

## The vocabulary
| Mode | Sequence | Result |
|---|---|---|
| send | open palm (held) -> fist (held) -> open the hand with a fast flick | `ready` -> `grab` -> `throw` (with direction and speed) |
| send | same, but the hand opens gently | `drop` (nothing is sent) |
| receive | fist (held) -> open the hand | `ready` -> `catch` (accept). Close your fist, then release it. A fist that is already closed when the offer arrives must open first. |
| any | tracking only | `idle` mode: poses and feedback, no gesture events |

Every gesture also has a button fallback in the app (Milestone 6).

## How it works
camera picture -> `HandTracker` (MediaPipe: 21 landmarks per hand) -> `GestureEngine` (pose + state machine) -> events.
The engine knows nothing about cameras, windows or the network. The app picks the mode:
`send` when a file is selected, `receive` while an offer is waiting, `idle` otherwise.

* **Open palm / fist**: a finger counts as stretched when its tip is much farther from the wrist than its knuckle
  (ratio above `extended_ratio`) and as curled when below `curled_ratio` (in between it keeps its previous answer).
  Uses MediaPipe's metric 3D "world" landmarks, so turning the hand does not matter.
* **Smoothing**: a pose must win a vote over `pose_window` seconds, then stay stable (`arm_time`, `grab_time`, ...).
* **Throw**: the hand's speed is measured in *palm lengths per second*, so it works at any distance from the camera.
  A release counts as a throw if the fastest movement in the `throw_window` before it reached `throw_speed`
  (or always, if `require_motion` is false: the simplest, most reliable variant).
* **No repeats**: a cooldown follows every throw/catch, and the whole gesture must be repeated.
* **Lost hand**: a hand may vanish for `lost_grace` seconds; longer cancels the armed gesture.
* **Several hands**: the closest one is followed; hands smaller than `min_palm_size` (far away) are ignored.
* All times are real seconds, so frame rate does not matter.

## Tuning (`gesture_config.json`, created by `--calibrate` or `s` in the demo)
| Symptom | Change |
|---|---|
| Throws are not recognised | lower `throw_speed`, or run `--calibrate`, or turn `require_motion` off |
| Throws trigger when you did not mean to | raise `throw_speed` |
| Fist not recognised | lower `fist_min_curled` to 3, or raise `curled_ratio` |
| Open palm not recognised | lower `open_min_fingers` to 3 |
| Too slow to react | lower `arm_time`, `grab_time`, `pose_window` |
| Flickers between states | raise `pose_window` / `pose_majority` |

## Honest limits
* Gesture recognition is never perfect. It depends on lighting, background, distance and camera quality.
* A hand moving very fast blurs, and MediaPipe may lose it for a moment (that is what `lost_grace` is for).
* Phone-as-webcam apps add delay (latency); expect the response to feel slightly behind your hand.
* The simulated-hand tests prove the logic. How well MediaPipe sees *your* hand in *your* room has to be tested with
  `python gesture_demo.py`.
