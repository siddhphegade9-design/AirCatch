# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for AirCatch: one folder (dist/AirCatch) holding AirCatch.exe and everything it needs.

    pyinstaller packaging/aircatch.spec --noconfirm

A folder, not a single .exe: it starts faster (nothing to unpack on every launch) and is flagged far
less often by antivirus programs. The installer and the portable zip are both made from this folder.
"""
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "aircatch" / "__init__.py").read_text("utf-8")).group(1)
WINDOWS = sys.platform == "win32"

# --- files the program reads at run time ------------------------------------------------------
datas = [
    (str(SRC / "aircatch" / "ui" / "main.qml"), "aircatch/ui"),          # the whole interface
    (str(SRC / "aircatch" / "ui" / "assets"), "aircatch/ui/assets"),     # logo and icon
]
# MediaPipe's hand-tracking models and graphs (.tflite, .binarypb). Without them the camera view
# opens but no hand is ever found. About 30 MB; all of MediaPipe's data is taken to be safe.
datas += collect_data_files("mediapipe")

# --- code PyInstaller cannot see by reading the imports ------------------------------------------
hiddenimports = collect_submodules("zeroconf")          # compiled modules that import each other
hiddenimports += ["mediapipe.python.solutions.hands", "qrcode.image.pil"]

# --- things that are installed but never used: leaving them out saves hundreds of MB ----------------
excludes = [
    "tkinter", "jax", "jaxlib", "scipy", "sounddevice", "torch", "tensorflow", "pandas", "IPython",
    "pytest", "_pytest", "sentencepiece",
    # Qt modules AirCatch does not use (it uses QtCore, QtGui, QtNetwork, QtQml, QtQuick and friends)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSerialBus", "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech", "PySide6.QtQuick3D",
    "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtWebView",
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# PyInstaller copies every QML module Qt ships. AirCatch imports five of them (QtQuick, Controls,
# Dialogs, Layouts, Shapes); the big unrelated ones are dropped here.
UNUSED_QML = ("QtQuick3D", "QtWebEngine", "QtWebView", "QtCharts", "QtDataVisualization", "QtGraphs",
              "QtMultimedia", "QtLocation", "QtPositioning", "QtSensors", "Qt3D", "QtTest", "QtPdf",
              "QtScxml", "QtRemoteObjects", "QtTextToSpeech", "QtWebSockets", "QtWebChannel",
              "QtBluetooth", "QtNfc", "QtQuick/VirtualKeyboard", "QtQuick/Scene3D", "QtQuick/Scene2D",
              "QtQuick/Timeline", "QtQuick/Particles", "QtQuick/Pdf")


# ...and the Qt libraries behind them, which get pulled in as dependencies of plugins nobody loads.
# The web engine alone is about 200 MB. (Names as on Windows "Qt6Pdf.dll" and Linux "libQt6Pdf.so.6".)
UNUSED_QT_LIBS = ("Qt6WebEngine", "Qt6Quick3D", "Qt6Pdf", "Qt6Graphs", "Qt6Charts", "Qt6DataVisualization",
                  "Qt6Multimedia", "Qt63D", "Qt6Location", "Qt6Positioning", "Qt6Sensors",
                  "Qt6VirtualKeyboard", "Qt6Designer", "Qt6Sql", "Qt6Test", "Qt6Bluetooth", "Qt6Nfc",
                  "Qt6SerialPort", "Qt6SerialBus", "Qt6WebSockets", "Qt6WebChannel", "Qt6WebView",
                  "Qt6RemoteObjects", "Qt6Scxml", "Qt6StateMachine", "Qt6TextToSpeech", "Qt6SpatialAudio",
                  "Qt6HttpServer", "Qt6Help", "Qt6UiTools", "Qt6QuickTimeline", "Qt6QuickParticles",
                  "Qt6QuickTest", "Qt6LabsWavefrontMesh")
UNUSED_FILES = ("qtwebengine", "QtWebEngineProcess",      # the web engine's resources, locales and helper
                # plugins that only work with libraries left out above (Qt would skip them with a warning)
                "qtvirtualkeyboardplugin", "qmltooling",
                "wavefrontmesh", "QtQml/StateMachine", "QtQml\\StateMachine", "LocalStorage")


def wanted(entry) -> bool:
    dest = entry[0].replace("\\", "/")
    name = dest.rsplit("/", 1)[-1]
    if "Qt6QmlLocalStorage" in name or name.startswith(("qpdf.", "libqpdf.")):
        return False
    stem = name[3:] if name.startswith("lib") else name
    if any(stem.startswith(lib) for lib in UNUSED_QT_LIBS) or any(part in dest for part in UNUSED_FILES):
        return False
    if "/qml/" not in "/" + dest:
        return True
    inside = dest.split("qml/", 1)[1]
    return not any(inside == name or inside.startswith(name + "/") for name in UNUSED_QML)


a.datas = [e for e in a.datas if wanted(e)]
a.binaries = [e for e in a.binaries if wanted(e)]

pyz = PYZ(a.pure)

version_file = None
if WINDOWS:                                             # what Windows shows under Properties > Details
    numbers = tuple(int(n) for n in (VERSION.split(".") + ["0"] * 4)[:4])
    version_file = str(Path(workpath) / "version_info.txt")
    Path(workpath).mkdir(parents=True, exist_ok=True)
    Path(version_file).write_text(f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'AirCatch'),
      StringStruct('FileDescription', 'AirCatch - gesture-controlled file transfer'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'AirCatch'),
      StringStruct('OriginalFilename', 'AirCatch.exe'),
      StringStruct('ProductName', 'AirCatch'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AirCatch",
    console=False,                                      # a window program: no black console window
    icon=str(ROOT / "packaging" / "aircatch.ico") if WINDOWS else None,
    version=version_file,
    upx=False,                                          # compressed .exe files set off antivirus alarms
)

coll = COLLECT(exe, a.binaries, a.datas, name="AirCatch", upx=False)
