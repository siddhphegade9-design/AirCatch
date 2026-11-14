# Changelog

All notable changes to AirCatch are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-14

The first public release: AirCatch as an installable Windows program.

### Added
- **Windows installer** (`AirCatch-Setup-1.0.0.exe`): choose the folder, Start menu and desktop
  shortcuts, firewall rule for private networks, uninstaller. Python is not needed.
- **Portable version** (`AirCatch-1.0.0-windows-portable.zip`): unzip and run.
- **First-start screen** explaining the camera, the firewall and pairing, with a camera test. It can
  be opened again from **Help** at the bottom of the window.
- **Ready indicator** above the camera view and in the file explorer: green when the next gesture
  will count, amber while the hand is on its way back.
- **Gesture file explorer**: peace sign to open, swipes to move, pinch to pick.
- Program icon, version number (shown in the window and in the file's properties), `--version`.
- Built-in self-test (`AirCatch.exe --self-test report.json`) used by the build to prove a packaged
  copy can open its window, track a hand, pair two devices and transfer a file.
- Automatic build and release on GitHub.

### Changed
- Swipes are judged one whole stroke at a time, with a cool-down for the hand coming back, and
  survive motion blur and low frame rates far better.
- The select pinch tolerates flicker and small movements; it needs about half a second.
- Pressing **Receive** puts away the chosen file.
- The app notices when the PC changes network and announces itself again.
- A camera that drops the odd frame keeps running; a busy window no longer makes the picture lag.

### Fixed
- Left swipes read as "select" or as right swipes when the hand turned edge-on.
- A second sender was told "said no" instead of "busy".
- The sender's file card stayed on screen after a completed transfer.

### Security
- Minimum versions of `cryptography`, `zeroconf` and `pillow` raised past known vulnerabilities.
- File names from other devices: invisible characters that disguise an extension are removed, and
  Windows device names such as `NUL.tar.gz` are caught.
- One machine can no longer occupy every connection slot.
- Received files are marked as coming from another computer (Windows warns before running them).
- A damaged identity or trust list no longer stops the app; a damaged identity is replaced and the
  user is told to pair again.

### Known limitations
- Not code-signed: Windows SmartScreen warns when the installer is run.
- Windows 10 and 11, 64-bit only.
- Gestures are tuned mostly on simulated hand movements; accuracy on your camera may differ.
- The device's private key is not encrypted on disk.
- PySide6 6.12.0 must not be used (it crashes the app on Python 3.11); the build refuses it.
