"""Файлы для визуальной проверки этапа 4 (папка out/stage4, в .gitignore).

    .venv\\Scripts\\python.exe tools\\preview_assets.py

contact_sheet.png - все значки в увеличении x6 с подписью ключа;
textures.png      - все плитки (каждая 2x1, виден стык);
legend_all.png/pdf - лист условных обозначений со всеми обозначениями.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from cormap import assets
from cormap.legend import render_legend
from cormap.notes import OBJECT_TYPES

OUT = Path(__file__).resolve().parent.parent / "out" / "stage4"
MAX_W = 1600
ZOOM = 6  # увеличение x6 относительно 4 px на мм бумаги


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("times.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet() -> Path:
    """Значки на светло-сером фоне: 1 мм бумаги = 6 * 4 px (x6 от 4 px/мм)."""
    px_per_mm = ZOOM * 4
    cells = []
    for t in OBJECT_TYPES:
        w, h = assets.icon_size_mm(t.key)
        im = Image.open(assets.icon_png(t.key)).convert("RGBA")
        im = im.resize((round(w * px_per_mm), round(h * px_per_mm)), Image.LANCZOS)
        cells.append((t.key, im))
    font = _font(15)
    cell_w = max(im.width for _, im in cells) + 24
    cols = max(1, MAX_W // cell_w)
    cell_h = max(im.height for _, im in cells) + 44
    rows = -(-len(cells) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (225, 225, 225))
    d = ImageDraw.Draw(sheet)
    for n, (key, im) in enumerate(cells):
        cx, cy = (n % cols) * cell_w, (n // cols) * cell_h
        sheet.paste(im, (cx + (cell_w - im.width) // 2, cy + 8), im)
        d.text((cx + 6, cy + cell_h - 30), key, fill=(0, 0, 0), font=font)
    path = OUT / "contact_sheet.png"
    sheet.save(path)
    return path


def textures_sheet() -> Path:
    items = [("soil/" + p.stem, p) for p in sorted(assets.SOIL_DIR.glob("*.png"))]
    items += [("ins/" + p.stem, p) for p in sorted(assets.INSULATION_DIR.glob("*.png"))]
    tile = 256  # плитка показана 256 px, повторена 2 раза по горизонтали
    cols = max(1, MAX_W // (tile * 2 + 16))
    rows = -(-len(items) // cols)
    font = _font(14)
    sheet = Image.new("RGB", (cols * (tile * 2 + 16), rows * (tile + 28)), (225, 225, 225))
    d = ImageDraw.Draw(sheet)
    for n, (label, p) in enumerate(items):
        im = Image.open(p).convert("RGB").resize((tile, tile), Image.LANCZOS)
        x, y = (n % cols) * (tile * 2 + 16), (n // cols) * (tile + 28)
        sheet.paste(im, (x, y))
        sheet.paste(im, (x + tile, y))
        d.text((x + 2, y + tile + 4), label, fill=(0, 0, 0), font=font)
    path = OUT / "textures.png"
    sheet.save(path)
    return path


def legend_files() -> list[Path]:
    fig = render_legend()
    png, pdf = OUT / "legend_all.png", OUT / "legend_all.pdf"
    fig.savefig(png, dpi=150)
    fig.savefig(pdf)
    return [png, pdf]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for p in [contact_sheet(), textures_sheet(), *legend_files()]:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
