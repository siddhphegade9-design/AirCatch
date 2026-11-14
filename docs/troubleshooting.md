# Troubleshooting

Start here: at the bottom of the AirCatch window, **Open log folder** shows `aircatch.log` (what the
app did) and `crash.log` (written only if it crashed). **Help** opens the first-start screen again.

## Installing

**"Windows protected your PC" when running the installer.**
AirCatch is not code-signed. Choose **More info**, then **Run anyway**. To check the download is the
published one, compare `Get-FileHash .\AirCatch-Setup-<version>.exe` with `SHA256SUMS.txt` on the
release page.

**Antivirus flags or removes AirCatch.**
Programs packaged with PyInstaller are sometimes flagged by mistake. Check the SHA-256 as above; if it
matches, add an exception or use the portable zip.

**Installing without administrator rights.**
In the installer choose **Install for me only**. Setup then cannot add the firewall rule, so Windows
asks about the firewall the first time AirCatch starts: choose **Allow**.

**Upgrading.** Run the new installer over the old one. Your pairings and settings are kept. Close
AirCatch first (Setup asks if it is still running).

**Uninstalling.** *Settings > Apps > Installed apps > AirCatch > Uninstall.* Setup asks whether to also
remove this device's identity and pairings (`%LOCALAPPDATA%\AirCatch`). Received files are never removed.

## The app does not start

- Look in `%LOCALAPPDATA%\AirCatch\crash.log` and `aircatch.log` (paste that path into Explorer).
- Very old graphics drivers can stop the window from appearing. Update the graphics driver.
- Portable version: unzip the **whole** folder first; `AirCatch.exe` does not work from inside the zip
  or without the `_internal` folder next to it.

## Camera

**"Camera unavailable" or the camera does not start.**
- *Windows Settings > Privacy & security > Camera*: turn on **Camera access** and **Let desktop apps
  access your camera**.
- Close other programs using the camera (Teams, Zoom, the Camera app, a browser tab).
- Laptops often have a camera shutter or a function key that switches the camera off.

**No camera at all.** AirCatch still works: use **Choose file**, **Send**, **Catch** and the buttons in
the file explorer.

**The camera stopped while in use.** It was unplugged or another program took it. Press **Start
camera** again.

## Devices cannot see each other

Work down this list on **both** PCs:

1. **Same network.** Both on the same Wi-Fi or the same phone hotspot. A "Guest" Wi-Fi usually keeps
   devices apart on purpose.
2. **Private network.** *Settings > Network & internet > Wi-Fi > (your network) > Network profile type
   > Private network.* On a network Windows calls *Public*, the firewall blocks AirCatch.
3. **Firewall.** *Windows Security > Firewall & network protection > Allow an app through firewall*:
   AirCatch must be ticked for **Private**. If it is missing, run AirCatch and accept the Windows
   prompt, or add `AirCatch.exe` by hand.
4. **Status.** The top of the window should say **Visible on this network · port ...**. If it says
   *Listening (not visible to others)*, the PC has no usable network address yet; it updates by itself
   a few seconds after Wi-Fi connects.
5. **Type the address.** Discovery (mDNS) is blocked on many office, school and hotel networks even
   when connections are allowed. On the receiving PC read the address from the status line
   (*port 5001*, and the PC's IP address from `ipconfig`), and on the sending PC type it into
   **Address if not found**, for example `192.168.1.23:5001`. **Test connection** tells you if it works.
6. **VPN.** A VPN often hides the local network. Disconnect it and try again.

## Pairing

- The code works **once**, for **two minutes**, and for three attempts. If it expired, press **Pair a
  device** again for a new one.
- Copy the **whole** code (it starts with `aircatch://`).
- "Not paired" when sending: one of the two PCs has forgotten the other (unpaired, reinstalled with
  its data removed, or its identity was reset). Pair again.
- *"This device's identity was damaged and has been reset"*: AirCatch found its key files unreadable,
  set them aside and made new ones. Pair your devices again.

## Transfers

- **"Busy"**: the other PC is already receiving something or showing another offer. Try again.
- **Nothing happens on the receiver**: the offer waits 60 seconds for **Catch** (or the catch gesture).
- **Stops part way**: the connection dropped. Nothing partial is kept; send again.
- **Too large**: the limit is 10 GB per file, and the receiver needs that much free disk space.
- Received files go to your **Downloads** folder. A file with the same name is saved as `name (1).ext`.
- Windows warns before opening a received program. That is intended.

## Gestures

- **Light first.** In dim light webcams slow down and blur fast movements; the hand then vanishes
  mid-swipe. A lamp in front of you fixes more than any setting.
- Keep your hand **in the middle** of the picture, about arm's length from the camera.
- **Swipes**: one quick movement, bring the hand back, wait for the green **READY**. The explorer's
  footer says why the last swipe did not count (for example *too short*).
- **Pinch**: thumb and index together, the other fingers up, palm towards the camera, held still for
  about half a second.
- **Peace sign**: index and middle up and apart, the others curled, held for a moment.
- **Throw**: the hand must open with a quick flick. **Easy throw** (bottom of the camera view) makes
  any opening of the fist count.
- Still unreliable? `docs/gestures.md` explains how to record your own movements and adjust the numbers
  in `gesture_config.json`.

## For developers

**The app crashes within seconds with "Fatal Python error: ... deallocating True or False".**
PySide6 **6.12.0** on Python 3.11 loses a reference to `True` on every signal emission. Use 6.11:

```
pip uninstall -y PySide6 PySide6_Addons PySide6_Essentials shiboken6 pyside6-pdf pyside6-webengine
pip install "PySide6>=6.7,<6.12"
pip check
```

(`pyside6-pdf` and `pyside6-webengine` exist only for 6.12; removing them is right. AirCatch uses
neither.) `tests/test_smoke.py` fails immediately if a bad version is installed.

**`mediapipe` has no attribute `solutions`.** A newer MediaPipe was installed. AirCatch needs
`mediapipe==0.10.14` (and so Python 3.11 and NumPy 1.26): `pip install -r requirements.txt`.

**The build says Inno Setup was not found.** `winget install JRSoftware.InnoSetup`, then run
`packaging\build.ps1` again. `-SkipInstaller` builds only the folder and the portable zip.

**The packaged program fails its self-test.** Open `dist\self-test.json`: each check says what failed.
