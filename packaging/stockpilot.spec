# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for "StockPilot".
#   pyinstaller packaging/stockpilot.spec --noconfirm --clean
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
APP_DISPLAY = "StockPilot"
EXE_NAME = "StockPilot"
VERSION = "1.1.0"

datas = [
    (str(ROOT / "stockpilot" / "assets"), "stockpilot/assets"),
    (str(ROOT / "stockpilot" / "ui" / "qml"), "stockpilot/ui/qml"),
]
hiddenimports = (
    ["stockpilot.ui.charts", "stockpilot.ui.controller", "stockpilot.ui.models", "PySide6.QtQuick", "PySide6.QtQml",
     "PySide6.QtQuickControls2", "PySide6.QtNetwork", "PySide6.QtSvg"]
    + collect_submodules("keyring.backends")
    + collect_submodules("alpaca")
    + collect_submodules("stockpilot")
    # imported dynamically (by anyio / by stockpilot.preload), so PyInstaller can't see them statically
    + ["anyio._backends._asyncio", "asyncio.windows_events", "asyncio.proactor_events", "asyncio.windows_utils",
       "sniffio", "msgpack", "websockets"]
)
excludes = [
    "tkinter", "matplotlib", "IPython", "pytest", "PyInstaller", "PIL", "jinja2",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtQuick3D", "PySide6.QtGraphs",
]

if sys.platform == "win32":
    icon = str(ROOT / "stockpilot" / "assets" / "icons" / "stockpilot.ico")
elif sys.platform == "darwin":
    icon = str(ROOT / "stockpilot" / "assets" / "icons" / "stockpilot.icns")
else:
    icon = None

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# Drop large Qt modules the app never uses (WebEngine alone is ~200 MB).
_DROP = ("WebEngine", "Qt63D", "/Qt3D", "Quick3D", "Qt6Pdf", "/QtPdf", "Multimedia", "Qt6Charts", "/QtCharts",
         "DataVisualization", "Qt6Graphs", "/QtGraphs", "Location", "Positioning", "Sensors", "TextToSpeech",
         "WebView", "WebChannel", "WebSockets", "RemoteObjects", "Scxml", "Qt6Test", "/QtTest", "Qt5Compat",
         "QtWayland", "SpatialAudio", "VirtualKeyboard", "Imagine", "FluentWinUI3", "Material", "Universal",
         "qtwebengine", "Qt/translations", "Qt6Designer", "Qt6Help", "Qt6Bluetooth", "Qt6Nfc", "Qt6SerialPort", "Qt6SerialBus")


def _keep(entry):
    name = entry[0].replace("\\", "/")
    return not any(d in name for d in _DROP)


a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,  # a real windowed app: no terminal window
    icon=icon,
    version=str(ROOT / "packaging" / "windows" / "version_info.txt") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=EXE_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_DISPLAY}.app",
        icon=icon,
        bundle_identifier="com.stockpilot.app",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_DISPLAY,
            "CFBundleDisplayName": APP_DISPLAY,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.finance",
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "12.0",
        },
    )
