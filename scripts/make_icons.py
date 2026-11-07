"""Render the StockPilot app icon to PNG / ICO / ICNS (run once; outputs are committed)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv)
for f in (ROOT / "stockpilot/assets/fonts").glob("SpaceGrotesk-*.ttf"):
    QFontDatabase.addApplicationFont(str(f))

from stockpilot.ui.icon import render_icon  # noqa: E402

out = ROOT / "stockpilot/assets/icons"
out.mkdir(parents=True, exist_ok=True)
render_icon(1024).save(str(out / "stockpilot-1024.png"))
render_icon(512).save(str(out / "stockpilot.png"))
render_icon(256, tray=True).save(str(out / "stockpilot-tray.png"))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
import io  # noqa: E402


def pil(size: int) -> Image.Image:
    """Each small size is rendered on its own (heavier line, no margin) instead of shrunk from the big one."""
    buf = QBuffer(ba := QByteArray())
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render_icon(size, tray=size <= 32).save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [pil(sz) for sz in sizes]
frames[-1].save(out / "stockpilot.ico", sizes=[(sz, sz) for sz in sizes], append_images=frames[:-1])
Image.open(out / "stockpilot-1024.png").convert("RGBA").save(out / "stockpilot.icns")
print("icons written to", out)
