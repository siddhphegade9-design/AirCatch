# AirCatch Milestone 11: Windows installer and public release

Goal: a person with a clean Windows laptop and no Python can install AirCatch, start it, find
another device, pair, and transfer files.

**Where this stands.** Everything needed to build the installer is written and was tested as far
as is possible without Windows. **The installer itself has not been built or run yet**: that needs
a Windows PC (or GitHub's Windows build machine). The section [Still to do on Windows](#still-to-do-on-windows)
is the list that turns "ready to build" into "done".

## 1. How the application is put together

| Layer | Where | Notes for packaging |
|---|---|---|
| Entry point | `run.py` -> `aircatch.app.main` | Starts Qt, loads `main.qml`, creates the controller |
| Interface | `src/aircatch/ui/main.qml`, `assets/` | Read from disk at start: must be in the package |
| App logic | `ui/controller.py` | Pure Python + Qt |
| Camera thread | `ui/vision_worker.py`, `vision/camera.py` | OpenCV (`cv2`) |
| Hand tracking | `gestures/tracker.py` | MediaPipe 0.10.14 with its model files (`.tflite`, `.binarypb`) |
| Network thread | `service.py`, `transfer/`, `pairing/`, `discovery/` | asyncio, `cryptography`, `zeroconf` (compiled parts) |
| Settings, identity, logs | `%LOCALAPPDATA%\AirCatch` | Outside the program folder, so upgrades keep them |
| Received files | the user's Downloads folder | |

Nothing is written next to the program, so it runs correctly from `C:\Program Files` without
administrator rights.

## 2. Packaging choices

| Choice | Why |
|---|---|
| **PyInstaller, one folder** (`dist\AirCatch`), not one file | Starts faster (nothing to unpack each time) and is flagged far less by antivirus. |
| **No console window** (`console=False`) | It is a window program. The app now copes with having no console at all. |
| **No UPX compression** | Compressed executables trigger antivirus false alarms. |
| **Inno Setup** installer | Free, scriptable, standard: folder choice, shortcuts, uninstaller, upgrade in place. |
| **Portable zip** as well | For people who cannot or will not install. |
| **Built by a script** (`packaging\build.ps1`) and by **GitHub Actions** | The same steps on your PC and on a clean machine; a tag publishes a release. |
| **Self-test inside the program** | A packaged copy has no console and no tests. `AirCatch.exe --self-test file.json` makes it prove it works. |

Files:

- `packaging/aircatch.spec`: the PyInstaller recipe. Bundles `main.qml`, the assets and all MediaPipe
  data; leaves out what is installed but unused (the Qt web engine alone is about 200 MB).
- `packaging/installer.iss`: the Inno Setup script.
- `packaging/build.ps1`: builds, checks, self-tests, zips, makes the installer, writes checksums.
- `packaging/make_icon.py`, `packaging/aircatch.ico`: the icon, made from the logo.
- `.github/workflows/release.yml`: tests + build on every push; publishes a release on a `v*` tag.

## 3. What was changed in the app

- `aircatch.__version__ = "1.0.0"`, shown in the window, in the `.exe` properties and by `--version`.
- No-console safety: a windowed `.exe` has no `stdout`; a stray `print` would have crashed it.
- Its own taskbar icon and a named mutex, so the installer can tell that AirCatch is running.
- `--self-test FILE`: opens the window, starts the network service, runs the hand tracker on a blank
  picture, makes a QR code, checks TLS 1.3, **pairs two devices and sends a file each way** on the
  same computer, writes the results to `FILE` and exits with 0 only if everything passed.
- First-start screen (camera, firewall, pairing), shown once; **Help** brings it back.
- Error messages no longer tell an installed user to run `pip`.
- Nothing was removed and no working module was rewritten.

## 4. Risks found, and what was done

| Risk | Outcome |
|---|---|
| **PySide6 6.12.0 crashes the app on Python 3.11.** Every signal emission loses a reference to `True`; after a few thousand the interpreter aborts. The app emits signals for every camera frame. `requirements.txt` allowed it, and `pip install --upgrade` installs it. | **Found while testing the packaging; fixed.** `requirements.txt` now says `PySide6>=6.7,<6.12`; the build refuses 6.12; a test fails at once if a bad version is installed. Confirmed on Linux with Python 3.11: 6.7.3, 6.8.3, 6.9.3, 6.10.2, 6.11.0 and 6.11.3 are fine, 6.12.0 is not. |
| MediaPipe model files missing from the package (camera opens, no hand is ever found) | All MediaPipe data is bundled; the build checks the models are there; the self-test runs the tracker. |
| QML or assets not found once packaged | Bundled at the paths the code already uses; the self-test loads the real window. |
| Compiled parts of `zeroconf` / `cryptography` not picked up | Collected explicitly; the self-test starts discovery, pairs and transfers over TLS. |
| Microsoft C++ runtime missing on a clean PC | The build copies the runtime DLLs into the package if PyInstaller did not. **Needs checking on a clean PC.** |
| Package too big | Unused Qt parts removed: 811 MB down to 573 MB as a folder on Linux (about 330 MB zipped). Windows sizes will differ. |
| Windows Firewall blocks receiving | The installer adds a rule for **private** networks; the first-start screen and the troubleshooting guide explain the rest. |
| SmartScreen and antivirus warnings | Cannot be removed without a paid code-signing certificate. Documented, with checksums to verify downloads. |
| Tests that depended on timing | Three were found by running on other machines and Python versions, and fixed. |

## 5. What was verified, and where

Be clear about the difference between these columns.

| Check | Linux, Python 3.11, exact library versions | Windows |
|---|---|---|
| Automated tests | 721 passed, 1 skipped | **You:** 709 passed before this milestone; run again |
| PyInstaller recipe builds a package | Yes | **Not yet run** |
| Packaged program: window loads | Yes (self-test) | **Not yet run** |
| Packaged program: hand tracker with bundled models | Yes (self-test) | **Not yet run** |
| Packaged program: pairs two devices and transfers a file each way (same computer) | Yes (self-test) | **Not yet run** |
| No kept Qt library depends on a removed one | Yes (checked every library) | **Not checked** (Windows libraries differ) |
| `build.ps1` | Syntax checked in PowerShell 7; its version, report and release-note logic run | **Not yet run** |
| `installer.iss` | Read carefully; **cannot be compiled without Windows** | **Not yet run** |
| GitHub workflow | Parses; **never run** | **Not yet run** |
| Camera, gestures, gesture explorer in the packaged program | Not possible (no camera) | **Not yet run** |
| Install, upgrade, uninstall | Not possible | **Not yet run** |
| Clean PC without Python | Not possible | **Not yet run** |
| Two physical laptops, large files, no camera, network failure | Not possible | **Not yet run** |

The Linux build proves the recipe is complete (nothing the program needs is left out) because the
same recipe and the same Python code are used on Windows. It does not prove the Windows build
works: the libraries, the file layout inside PySide6 and the system DLLs are different there.

## Still to do on Windows

Do these in order. Stop and report at the first one that fails: each has a clear failure message.

**A. Build**
```powershell
cd C:\Projects\aircatch
.venv\Scripts\Activate.ps1
pip install -r requirements.txt pyinstaller
python -m pytest
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```
Expected: "Self-test passed." with eight `ok` lines, then `dist\installer\AirCatch-Setup-1.0.0.exe`
and `dist\AirCatch-1.0.0-windows-portable.zip`.

**B. On the build PC**
- [ ] Run the installer: choose a folder, finish, AirCatch starts.
- [ ] Start menu and desktop shortcuts exist and show the icon.
- [ ] The first-start screen appears once; **Try the camera** works; gestures work as from source.
- [ ] *Windows Security > Firewall > Allow an app*: AirCatch is listed for Private.
- [ ] Right-click `AirCatch.exe` > Properties > Details shows version 1.0.0.
- [ ] Run the installer again over the top (upgrade): pairings are kept.
- [ ] Uninstall from Settings: the program folder and shortcuts are gone; answer **No** and check
      `%LOCALAPPDATA%\AirCatch` is still there; install again and the pairing still works.

**C. On the second PC, which must not have Python** (this is the definition of done)
- [ ] Copy only `AirCatch-Setup-1.0.0.exe` to it and install.
- [ ] It starts. If it does not: `%LOCALAPPDATA%\AirCatch\crash.log`, and note any "missing DLL" message.
- [ ] Each PC sees the other; pair them.
- [ ] Send a small file each way; send a file of 1 GB or more; cancel one part way.
- [ ] Turn Wi-Fi off and on during idle: the device comes back by itself.
- [ ] Unplug or disable the camera: a clear message, the buttons still work.
- [ ] Try the portable zip on one PC.

**D. Publish**
1. Create the repository on GitHub and push the code (check that no `devices/` folder or `.key` file is included).
2. In the repository settings turn on **Security > Private vulnerability reporting** (SECURITY.md points to it).
3. Decide on a license and add a `LICENSE` file (the README says none has been chosen).
4. `git tag v1.0.0` and `git push origin v1.0.0`. GitHub tests, builds, self-tests and publishes the
   release with the installer, the zip and `SHA256SUMS.txt`. Watch the run under **Actions**: this
   workflow has never run, so expect to fix something small the first time.
5. Or publish by hand: **Releases > Draft a new release**, upload the files from `dist\`.

## Known limitations of this release

- Not code-signed: SmartScreen warns; some antivirus programs may flag it.
- Windows 10 and 11, 64-bit only.
- The download is large (hundreds of MB) because it carries Qt, OpenCV and MediaPipe.
- No automatic updates: a new version is installed by running the new installer.
- The private key is not encrypted on disk (see `docs/milestone-10.md`).
- Gestures are tuned mostly on simulated hand movements.
