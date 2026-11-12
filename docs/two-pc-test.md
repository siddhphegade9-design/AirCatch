# Testing AirCatch on two PCs

Everything so far was tested on one computer. This guide is the real test: two physical laptops,
two network cards, a router and two firewalls. Do the steps in order and write your results in the
record sheet at the end.

Call the laptops **PC-1** and **PC-2**. Both must be able to send and receive.

## 0. What you need

- Two Windows PCs, both with the project and Python 3.11 (PC-2 setup is step 1).
- One Wi-Fi network that both join. Best choice for a first test: a **phone hotspot** (home routers
  sometimes isolate devices and college Wi-Fi almost always does).
- A way to move one line of text from PC-1 to PC-2 (WhatsApp Web, Teams, email to yourself, or a USB stick).
- Optional: a camera on each PC (the buttons work without one).
- A test picture, a small text file, and one large file (step 8).

## 1. Prepare PC-2

1. Install Python 3.11 from python.org (tick **Add python.exe to PATH**).
2. Copy the project to PC-2, for example to `C:\Projects\aircatch`. Either `git clone` your repository or
   copy the folder as a zip. **Leave out** `.venv`, `devices`, `__pycache__` and `.pytest_cache`.
3. **Never copy PC-1's identity to PC-2.** The identity lives in `%LOCALAPPDATA%\AirCatch` (not in the
   project folder). Each PC must create its own the first time it runs. Two PCs with the same identity
   would be the same device.
4. In PowerShell on PC-2:

```powershell
cd C:\Projects\aircatch
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest
```

   MediaPipe is a big download, allow a few minutes. Expect all tests to pass (one skipped is normal on Windows).
   On a PC without a camera the app still runs; you just use the buttons.

## 2. Network checks (5 minutes that save an hour)

1. Connect both PCs to the same network. Run `ipconfig` on each and note the **IPv4 Address** of the
   Wi-Fi adapter. The first three numbers must match (for example `192.168.43.x` on both).
2. Set the network to **Private** on both PCs: Settings > Network & internet > Wi-Fi > your network >
   Network profile type > **Private network**. To check: `Get-NetConnectionProfile`.
3. Start AirCatch on both PCs (step 3). Windows will ask about the firewall the first time. Tick
   **Private networks** and click **Allow access**. If you missed the popup, open PowerShell **as
   administrator** and run (use the path printed by the first command):

```powershell
python -c "import sys; print(sys._base_executable)"
New-NetFirewallRule -DisplayName "AirCatch (Python)" -Direction Inbound -Program "C:\PASTE\THE\PATH\python.exe" -Action Allow -Profile Private
```

4. With AirCatch running on PC-2, check from PC-1 that the door is open (use PC-2's real IP):

```powershell
Test-NetConnection 192.168.43.20 -Port 5001
```

   You want `TcpTestSucceeded : True`. If it says False, nothing in AirCatch can work yet: fix the
   network, profile or firewall first (see troubleshooting). Do not rely on `ping`; Windows blocks it
   by default even when everything else works.

## 3. Start AirCatch on both PCs

Give each PC a different name and a fixed port (a fixed port makes the firewall and the manual address simple):

```powershell
# PC-1
cd C:\Projects\aircatch ; .venv\Scripts\Activate.ps1
python run.py --name PC-1 --port 5001

# PC-2
cd C:\Projects\aircatch ; .venv\Scripts\Activate.ps1
python run.py --name PC-2 --port 5001
```

The window title shows the device name. The pill at the top should say
**Visible on this network · port 5001** with a green dot. A yellow dot means the network service
did not start; read the message in the pill.

Received files go to `Downloads\AirCatch` on each PC.

## 4. Pair (once per pair of PCs)

1. **PC-1:** click **Pair a device**, then **Copy code**. The code works for **2 minutes and one use**.
2. Move the code to PC-2 (paste it into a chat to yourself, open it on PC-2, copy it).
3. **PC-2:** click **Join with code**, **Paste**, **Join**.
4. **PC-1:** a box asks **Allow this device to pair?** Check that the name says PC-2, then **Allow**.
5. PC-2 shows a green **Paired with PC-1** and closes the box. Both device lists now show the other PC.

Expected: within about 5 seconds the other PC shows **ONLINE** (green dot). If it stays **OFFLINE**
(grey dot), discovery is blocked on this network. That is fine: type PC-2's address and port,
for example `192.168.43.20:5001`, into the **Address** box of the Devices card on the sending PC.
Sending still works. Clear the box when you want automatic discovery again.

If joining fails, check the address inside the code (`h=...`): it must be PC-1's Wi-Fi address from `ipconfig`.

## 5. Test A: PC-1 sends a picture to PC-2 (buttons only)

1. PC-1: drop a picture on the window (or **Choose file**). The card shows the real picture.
2. PC-1: select PC-2 in the list and click **Send to PC-2**.
3. PC-1 sees the card fly away and **Waiting for PC-2 to catch...**
4. PC-2: the same picture should **rise from the bottom into the middle**. Click **Catch**.
5. PC-2: a progress ring fills, then the file **zooms in** with a green check. Click **Show folder**.
6. PC-1: **Delivered**.

Verify the file is identical. On both PCs run (use your own paths):

```powershell
Get-FileHash "C:\path\to\original.jpg" -Algorithm SHA256
Get-FileHash "$env:USERPROFILE\Downloads\AirCatch\original.jpg" -Algorithm SHA256
```

The two hashes must be identical.

## 6. Test B: the other direction

Repeat step 5 with the roles swapped (PC-2 sends to PC-1). A text file is good for this one; the
receiver shows a coloured file-type badge instead of a picture.

## 7. Test C: gestures

Camera on PC-1 (sender) and, if it has one, PC-2 (receiver). Good light on your hand, plain background,
40 to 80 cm from the camera.

1. PC-1: **Start camera**, make sure **SEND** is highlighted, choose a picture, select PC-2.
2. PC-1: open palm for about half a second (the progress bar fills, state READY), close it into a fist
   (**the card zooms out and follows your hand**), then flick sideways while opening the hand
   (**the card flies away**, a real send starts).
3. PC-2: **Start camera** (the app switches itself to RECEIVE when something arrives). When the picture
   rises, hold an open palm towards the camera for about half a second, then close it into a fist.
   That is the catch.
4. If a gesture misbehaves, run `python gesture_demo.py --calibrate` on that PC, or use the buttons.

Write down: how many of 10 throws worked, how many catches worked, and whether anything triggered by accident.

## 8. Test D: things going wrong (these matter as much as the happy path)

| Do this | Expected |
|---|---|
| PC-2 clicks **Decline** | PC-1 shows "The other device said no." The card comes back. |
| PC-2 ignores the offer for 60 seconds | PC-1 shows a failure ("said no" or "stopped responding", whichever timer fires first). PC-2's card closes by itself. |
| Send the same file twice | PC-2 saves `name.jpg` and then `name (1).jpg`. Nothing is overwritten. |
| PC-2 selects PC-1, clicks **Unpair**, then **Sure?**; then PC-1 sends | PC-1 says the devices are not paired. Nothing arrives. Re-pair to continue. |
| Close the AirCatch window on PC-2, then PC-1 sends | PC-1 tells you PC-2 is not visible (or, with an address typed, that it could not connect). Nothing is sent. |
| Use a pairing code twice, or wait over 2 minutes | Joining says the code is wrong or the window is closed. |
| Large file, then close PC-2's app halfway | PC-1 shows a failure ("connection was lost"). PC-2's `Downloads\AirCatch` has no half file, and `.aircatch-incoming` is empty. |
| Large file, then turn PC-2's Wi-Fi off halfway | PC-1 fails after up to about 30 seconds. No half file on PC-2. |

Make a large test file without administrator rights:

```powershell
mkdir C:\Temp -ErrorAction SilentlyContinue
$f = [IO.File]::Create("C:\Temp\big500.bin"); $f.SetLength(500MB); $f.Close()
```

**Speed:** send `big500.bin` with a stopwatch. Speed in MB/s is 500 divided by the seconds. Real
Wi-Fi speed depends on the network (5 GHz is much faster than 2.4 GHz). Note the number; it is a
measurement of your setup, not a promise.

## 9. Record sheet (send this back)

| Check | Result |
|---|---|
| `Test-NetConnection` to PC-2 port 5001 | True / False |
| Network used (home Wi-Fi, hotspot, campus) | |
| Pairing worked first time | yes / no (message) |
| Device showed ONLINE (seconds) or stayed OFFLINE | |
| Picture PC-1 to PC-2 arrived, hashes equal | yes / no |
| Picture or file PC-2 to PC-1 arrived | yes / no |
| Preview picture appeared on the receiver | yes / no |
| Gesture throws that worked (of 10) | |
| Gesture catches that worked (of 10) | |
| False triggers | |
| 500 MB time and MB/s | |
| Decline / timeout / unpair / interrupted behaved as in the table | yes / no (which?) |
| Animation smooth? (Reduce motion needed?) | |
| Anything that crashed or showed an error | |

## 10. Troubleshooting

| Problem | Most likely cause and fix |
|---|---|
| `Test-NetConnection` says False | Firewall (allow Python on **Private**), the network is **Public** (set it to Private), the PCs are on different networks, or the router isolates devices. Try a phone hotspot. |
| Joining says it could not connect | The address in the code is not PC-1's Wi-Fi address (check `h=` against `ipconfig`), or the firewall on PC-1 blocks it. |
| "Wrong or already used pairing code" | Codes work once for 2 minutes. Click **Pair a device** again for a new one. |
| Paired but always OFFLINE | Discovery (mDNS) blocked. Use the **Address** box. Or start with `python run.py --interface <this PC's Wi-Fi IP>`. |
| "That is not the one you paired with" (`untrusted_peer`) | A device was reinstalled or its config folder was changed. Unpair on both, pair again. |
| Nothing happens when you throw | You need a file chosen and a **paired, ONLINE** device selected (or an address typed). A message at the bottom says which one is missing. |
| Camera does not open | Close other apps using it. Check Settings > Privacy & security > Camera. Run `python gesture_demo.py --list-cameras` and start with `--camera N`. |
| Gesture feels slow or misses | Better light, plain background. Run `python gesture_demo.py --calibrate`. |
| Animations stutter | Switch on **Reduce motion** (Activity card). Tell me the PC model; I'll tune it. |

## 11. For demo day

- Test on the **exact network** you will use. Campus and office Wi-Fi usually blocks device-to-device traffic.
  A phone hotspot (or Windows Mobile Hotspot on one laptop) is the dependable choice.
- Pair the laptops **before** the demo and leave them paired. Pairing survives restarts.
- Start both apps with the same commands you used in the test (`--name` and `--port 5001`).
- Keep the button path ready: if the camera misbehaves, **Send** and **Catch** do the same real transfer.
- If the room's network misbehaves, type the address into the Address box and carry on.
