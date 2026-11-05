"""Render the 'dk.' app icon to PNG / ICO / ICNS (run once; outputs are committed)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv)
for f in (ROOT / "dkapp/assets/fonts").glob("SpaceGrotesk-*.ttf"):
    QFontDatabase.addApplicationFont(str(f))

from dkapp.ui.icon import render_icon  # noqa: E402

out = ROOT / "dkapp/assets/icons"
out.mkdir(parents=True, exist_ok=True)
render_icon(1024).save(str(out / "dk-1024.png"))
render_icon(512).save(str(out / "dk.png"))
render_icon(256, tray=True).save(str(out / "dk-tray.png"))

from PIL import Image  # noqa: E402

big = Image.open(out / "dk-1024.png").convert("RGBA")
big.save(out / "dk.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
big.save(out / "dk.icns")
print("icons written to", out)
