# AirCatch gestures (Milestone 5)

## The vocabulary
| Mode | Sequence | Result |
|---|---|---|
| send | open palm (held) -> fist (held) -> open the hand with a fast flick | `ready` -> `grab` -> `throw` (with direction and speed) |
| send | same, but the hand opens gently | `drop` (nothing is sent) |
| receive | fist (held) -> open the hand | `ready` -> `catch` (accept). Close your fist, then release it. A fist that is already closed when the offer arrives must open first. |
| any | tracking only | `idle` mode: poses and feedback, no gesture events |

| send | peace sign (index + middle up) held 0.5 s | `browse_open`: the file explorer opens (never while a file is in hand) |
| browse | quick hand swipe up / down / left / right (any hand shape except a pinch) | `swipe` with `detail` = direction. Up/down move the highlight, right opens a folder, left goes back |
| browse | pinch (thumb tip + index tip touching, middle / ring / pinky straight up) held 0.25 s | `select`: opens a folder, or picks a file, closes the explorer and returns to send mode |
| browse | peace sign held 0.5 s | `browse_close` |

Every gesture also has a button fallback in the app (Milestone 6). The explorer has a **Browse** button and its rows can be clicked.

### Hand turned sideways, or fingertips pointing at the camera
* Open/fist, pinch and peace sign are judged from MediaPipe's 3D hand points, which do not change much when the hand turns.
  Tests cover hands turned up to about 70 degrees both ways.
* Swipe speed is measured against a hand scale that stays stable when the palm is edge-on or foreshortened.
* Swipes follow the average of all 21 hand points (so fingertips swinging in a wrist flick count) and do not
  need the hand to look like an open palm (fingers pointing at the camera often read as a fist).
* Up/down swipes have their own, easier limits (`swipe_travel_vertical` 0.85 and `swipe_speed_vertical` 2.6, against 0.9 and 2.8 sideways) because
  they are smaller than sideways ones. While the explorer is open, the footer shows why the last swipe was
  accepted or rejected, for example `down: too short (0.4 of 0.6)`. Use it to tune the numbers in `gesture_config.json`.
* A thumbs up can also select: set `"select_with_thumbs_up": true`. Set `"select_with_pinch": false` to turn the pinch off.
* Swipes are judged one whole stroke at a time (`gestures/swipe.py`). A stroke starts when the hand speeds up
  (`swipe_start_speed`) and ends when it stops, slows right down, turns back, sets off in a new direction, or leaves
  the picture. Only then is it judged, by where it went from start to end, so the curl at the end of an arm arc
  cannot turn "down" into "left".
* A swipe needs: distance `swipe_travel` (1.0 palm sideways) / `swipe_travel_vertical` (0.8), top speed `swipe_speed`
  (3.0 palm/s) / `swipe_speed_vertical` (2.6), within about 37 degrees of its axis (`swipe_axis_share` 0.8), and its
  middle 80% done within `swipe_core_time` (0.33 s), so moving your hand to a new spot is not a swipe.
* Cooldown for the way back: after a swipe, the next stroke back the way it came is "the return" and never a swipe,
  however fast. The cooldown ends when the hand has come back and paused (`swipe_rest_time` 0.15 s) or
  `swipe_return_settle` (0.35 s) after coming back, or after resting `swipe_recentre` (2.5 s) anywhere. Swiping the same way
  again (down, back, down) always works. A stroke that was nearly a swipe gets the same cooldown, so a missed swipe
  never turns into a swipe the wrong way.
* Losing the hand for a moment (blur) does not reset this, and a stroke is joined up across missing frames
  (`swipe_rejoin` 0.3 s). A fast stroke that leaves the picture needs only 60% of the distance (`swipe_exit_ease`).
  A hand that just came into view does not swipe.
* A stroke that is plainly a swipe already (1.2x the needed distance, fast, straight) counts at once, while the hand
  is still moving (`swipe_early`); anything doubtful waits for the end of the stroke. This is what makes it feel immediate.
* A stroke that vanishes within `swipe_exit_reach` (1.5 palms) of the side or top edge it was heading for needs only
  `swipe_exit_travel` (0.4 palm): the hand swiped out of the picture. Not downwards (lowering the hand to rest).
* A command given straight after a swipe, before the hand came back or paused, is ignored, and so is its way back.
* A hand that vanished in the middle of the picture and comes back in at an edge swiped out that way, too fast for
  the camera: that counts (a hand simply taken away is followed to the edge and vanishes there). A swipe out of the
  picture is counted once, not again when the hand comes back in.
* Motion blur: a webcam often loses the hand during the fast part of a swipe. If the hand vanishes and is back
  somewhere else within `swipe_rejoin_long` (0.8 s), that jump is judged as the swipe (and its return gets the cooldown).
* Select = pinch held for `select_hold` (0.4 s; about half a second in all, counting the moment it takes to be
  recognised). Made for real cameras: it starts when the thumb and index tips meet with at least
  `pinch_straight_fingers` (2) of the other three fingers straight, and then carries on until the tips clearly part,
  even if the tracker misreads a finger. It may flicker off for up to `select_grace` (0.25 s) without starting again.
  The palm must stay within `select_drift` (0.5 palm) of where the pinch began (a hand being moved about is not
  selecting). After a select, let go for `select_release` (0.5 s) before the next one.
* Pinch and peace sign only count while the hand is held still (`gesture_still_speed`, `gesture_still_time`) with
  the palm towards the camera (`gesture_facing_min`). A hand turned edge-on while swiping often LOOKS like a pinch or a
  peace sign to MediaPipe (fingers overlapping); before this rule that fired selects and cancelled the swipe, and the
  hand coming back then swiped the wrong way.
* To tune for a real camera: `python gesture_record.py` prompts you through about 3 minutes of gestures, prints a
  score and saves `aircatch-recording-<date>.json.gz` (hand landmark numbers only, no pictures).
  `python gesture_record.py --replay FILE` scores it again with the current settings.
* Best results: start swipes from the middle of the picture, and pause for a moment after bringing the hand back.
* The footer shows the reason for the last stroke, e.g. `right: too short (0.8 of 1.0)`, `left: ignored (hand returning)`.
  If a `gesture_config.json` exists (in the app's settings folder or the start folder), its values override these
  defaults: delete it to get the new ones.
* A hand exactly edge-on (90 degrees) is the weak spot: the fingers hide each other and MediaPipe has to guess depth.
  A hand turned about 45-60 degrees is much steadier.
* The 21 hand points are smoothed (`smooth_landmarks`) and MediaPipe runs its more accurate model (`model_complexity = 1`).

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
