"""Сборка ресурсов: PNG значков из SVG и плитки текстур (ТЗ, раздел 7).

Запуск одной командой, повторный запуск даёт те же файлы:

    .venv\\Scripts\\python.exe tools\\build_assets.py

Растеризатор SVG - `resvg-py` (готовые wheel'ы для Windows и Linux, без системных
библиотек и шрифтов). Он нужен только для сборки (dev-зависимость): собранные PNG
лежат в проекте, и в работе сервиса растеризатор не требуется.

Текстуры грунта и штриховки изоляции генерируются программно и детерминированно
(фиксированные зёрна). Грунты нарисованы по мотивам образцов 19-24 и являются
ВРЕМЕННЫМИ: до утверждения заказчиком (вопрос 2 ТЗ, раздел 14) их надо заменить
или принять.
"""

from __future__ import annotations

import io
import math
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "cormap" / "assets"
SVG_DIR = ASSETS / "icons" / "svg"
PNG_DIR = ASSETS / "icons" / "png"
SOIL_DIR = ASSETS / "textures" / "soil"
INS_DIR = ASSETS / "textures" / "insulation"

DPI = 600  # разрешение PNG значков в размере на бумаге
TILE = 512  # сторона плитки текстуры, px
MM_PER_INCH = 25.4


# --------------------------------------------------------------------------- значки


def _viewbox_mm(svg_text: str) -> tuple[float, float]:
    m = re.search(r'viewBox="\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*"', svg_text)
    if not m:
        raise ValueError("в SVG нет viewBox вида «0 0 W H»")
    return float(m.group(1)), float(m.group(2))


def px_size(width_mm: float, height_mm: float, dpi: int = DPI) -> tuple[int, int]:
    """Размер PNG в пикселях для заданного размера на бумаге (с округлением вверх)."""
    return (
        math.ceil(width_mm * dpi / MM_PER_INCH - 1e-6),
        math.ceil(height_mm * dpi / MM_PER_INCH - 1e-6),
    )


def build_icons() -> int:
    import resvg_py

    PNG_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for svg in sorted(SVG_DIR.glob("*.svg")):
        text = svg.read_text(encoding="utf-8")
        w_mm, h_mm = _viewbox_mm(text)
        w_px, h_px = px_size(w_mm, h_mm)
        # resvg-py не понимает единицы «mm» в width/height корня: размер задаётся
        # в пикселях через параметры, а viewBox остаётся единственным источником пропорций
        body = re.sub(
            r'(<svg[^>]*?)\s+width="[^"]*"\s+height="[^"]*"', lambda m: m.group(1), text, count=1
        )
        data = resvg_py.svg_to_bytes(
            svg_string=body, width=w_px, height=h_px, skip_system_fonts=True
        )
        img = Image.open(io.BytesIO(bytes(data))).convert("RGBA")
        if img.size != (w_px, h_px):  # страховка от расхождения в 1 px
            img = img.resize((w_px, h_px), Image.LANCZOS)
        img.save(PNG_DIR / f"{svg.stem}.png", dpi=(DPI, DPI), optimize=True)
        count += 1
    return count


# ------------------------------------------------------------------ вспомогательное


def _grid() -> tuple[np.ndarray, np.ndarray]:
    y, x = np.mgrid[0:TILE, 0:TILE].astype(np.float64)
    return x, y


def _pnoise(rng: np.random.Generator, sx: float, sy: float | None = None) -> np.ndarray:
    """Периодический (бесшовный) шум: белый шум, сглаженный в частотной области."""
    sy = sx if sy is None else sy
    white = rng.standard_normal((TILE, TILE))
    fx = np.fft.fftfreq(TILE)[None, :]
    fy = np.fft.fftfreq(TILE)[:, None]
    flt = np.exp(-2.0 * math.pi**2 * ((sx * fx) ** 2 + (sy * fy) ** 2))
    out = np.real(np.fft.ifft2(np.fft.fft2(white) * flt))
    return _norm(out)


def _norm(a: np.ndarray) -> np.ndarray:
    """Растяжка в 0..1 по перцентилям 1 и 99."""
    lo, hi = np.percentile(a, [1, 99])
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _ramp(v: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    """Цветовая шкала: v в 0..1 -> RGB uint8."""
    xs = [s[0] for s in stops]
    chans = [np.interp(v, xs, [_hex(s[1])[i] for s in stops]) for i in range(3)]
    return np.clip(np.stack(chans, axis=-1), 0, 255).astype(np.uint8)


def _voronoi(
    rng: np.random.Generator,
    cells: int,
    jitter: float = 0.8,
    warp: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Бесшовная диаграмма Вороного на торе: (d1, d2, номер ячейки)."""
    x, y = _grid()
    if warp is not None:
        x = (x + warp[0]) % TILE
        y = (y + warp[1]) % TILE
    step = TILE / cells
    pts = []
    for i in range(cells):
        for j in range(cells):
            pts.append(
                (
                    (i + 0.5 + (rng.random() - 0.5) * jitter) * step,
                    (j + 0.5 + (rng.random() - 0.5) * jitter) * step,
                )
            )
    d1 = np.full((TILE, TILE), np.inf)
    d2 = np.full((TILE, TILE), np.inf)
    idx = np.zeros((TILE, TILE), dtype=np.int32)
    for k, (px, py) in enumerate(pts):
        dx = np.abs(x - px)
        dx = np.minimum(dx, TILE - dx)
        dy = np.abs(y - py)
        dy = np.minimum(dy, TILE - dy)
        d = np.hypot(dx, dy)
        closer = d < d1
        d2 = np.where(closer, d1, np.minimum(d2, d))
        idx = np.where(closer, k, idx)
        d1 = np.where(closer, d, d1)
    return d1, d2, idx


def _smooth(edge0: float, edge1: float, v: np.ndarray) -> np.ndarray:
    t = np.clip((v - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ------------------------------------------------------------------ грунты (временные)


def soil_peschanyi() -> np.ndarray:
    rng = np.random.default_rng(101)
    x, y = _grid()
    phase = 2 * math.pi * (3 * x - 6 * y) / TILE + 3.0 * (_pnoise(rng, 30) - 0.5) * 2
    s = (0.5 + 0.5 * np.sin(phase)) ** 1.6
    v = np.clip(0.8 * s + 0.12 * _pnoise(rng, 1.2) + 0.1 * _pnoise(rng, 8), 0, 1)
    return _ramp(v, [(0, "#4a2208"), (0.3, "#a8561a"), (0.6, "#e08a22"), (1, "#f4b64e")])


def soil_skalnyi() -> np.ndarray:
    rng = np.random.default_rng(102)
    warp = (14 * (_pnoise(rng, 40) - 0.5), 14 * (_pnoise(rng, 40) - 0.5))
    d1, d2, idx = _voronoi(rng, 5, 0.85, warp)
    tone = rng.uniform(0.35, 0.75, idx.max() + 1)[idx]
    edge = d2 - d1
    shade = 0.72 + 0.28 * _smooth(0, 40, edge)
    v = tone * shade + 0.1 * (_pnoise(rng, 2.0) - 0.5) + 0.06 * (_pnoise(rng, 10) - 0.5)
    v = v * _smooth(1.0, 5.0, edge) * 0.85 + 0.0
    return _ramp(np.clip(v, 0, 1), [(0, "#33373b"), (0.45, "#7c8085"), (1, "#c2c4c7")])


def soil_suglinok() -> np.ndarray:
    rng = np.random.default_rng(103)
    warp = (12 * (_pnoise(rng, 25) - 0.5), 12 * (_pnoise(rng, 25) - 0.5))
    d1, d2, idx = _voronoi(rng, 10, 0.95, warp)
    tone = rng.uniform(0.0, 1.0, idx.max() + 1)[idx]
    edge = d2 - d1
    base = 0.5 * _pnoise(rng, 5) + 0.25 * _pnoise(rng, 1.5) + 0.25 * tone
    crack = 1.0 - _smooth(0.8, 4.2, edge)
    v = base * (0.8 + 0.2 * _smooth(0, 22, edge))
    rgb = _ramp(np.clip(v, 0, 1), [(0, "#a85a30"), (0.5, "#cd8650"), (1, "#e9b284")])
    dark = np.array(_hex("#43210f"), dtype=np.float64)
    return (rgb * (1 - crack[..., None]) + dark * crack[..., None]).astype(np.uint8)


def soil_supes() -> np.ndarray:
    rng = np.random.default_rng(104)
    v = 0.4 * _pnoise(rng, 14) + 0.3 * _pnoise(rng, 5) + 0.3 * _pnoise(rng, 1.5)
    spots = (_pnoise(rng, 1.0) > 0.88).astype(np.float64)
    v = np.clip(v - 0.25 * spots, 0, 1)
    return _ramp(_norm(v), [(0, "#4f3b20"), (0.5, "#9a7c4a"), (1, "#d6ba86")])


def soil_torf() -> np.ndarray:
    rng = np.random.default_rng(105)
    blades = 0.55 * _pnoise(rng, 1.1, 7) + 0.45 * _pnoise(rng, 2.0, 5)
    v = np.clip(0.8 * _norm(blades) + 0.3 * (_pnoise(rng, 30) - 0.5), 0, 1)
    return _ramp(v, [(0, "#164a10"), (0.5, "#3f9a26"), (1, "#92e052")])


def soil_ilistyi() -> np.ndarray:
    rng = np.random.default_rng(106)
    x, y = _grid()
    low = _pnoise(rng, 35)
    band = 0.5 + 0.5 * np.cos(2 * math.pi * (x + y) / TILE - 1.0 + 1.2 * (low - 0.5))
    v = 0.45 * low + 0.4 * band**1.5 + 0.15 * _pnoise(rng, 7)
    return _ramp(_norm(v), [(0, "#44474c"), (0.5, "#85888d"), (1, "#cbcdd0")])


# ------------------------------------------------------------------ изоляция


def ins_bitumnoe() -> Image.Image:
    """Чёрный фон, белые прямоугольники в шахматной решётке."""
    img = Image.new("L", (TILE, TILE), 0)
    d = ImageDraw.Draw(img)
    cell = 32
    for j in range(TILE // cell):
        for i in range(TILE // cell):
            cx = i * cell + 16 + (cell // 2 if j % 2 else 0)
            cy = j * cell + 16
            for ox in (-TILE, 0, TILE):  # перенос через край для бесшовности
                d.rectangle([cx - 8 + ox, cy - 5, cx + 7 + ox, cy + 4], fill=255)
    return img.convert("RGB")


def ins_zavodskoe() -> Image.Image:
    return Image.new("RGB", (TILE, TILE), (0, 0, 0))


def ins_lakokrasochnoe() -> Image.Image:
    """Белый фон, чёрные горизонтальные зигзаги."""
    img = Image.new("L", (TILE, TILE), 255)
    d = ImageDraw.Draw(img)
    wave = 32
    for j in range(TILE // wave):
        y0 = j * wave + wave // 2
        pts = [
            (i * wave // 2, y0 + (-9 if i % 2 == 0 else 9)) for i in range(TILE // (wave // 2) + 1)
        ]
        d.line(pts, fill=0, width=5, joint="curve")
    return img.convert("RGB")


def _stripes(period: int, width: float, diag: int, on: float, off: float) -> np.ndarray:
    """Диагональные полосы с мягкой кромкой; diag=+1 вдоль x+y, -1 вдоль x-y."""
    x, y = _grid()
    t = ((x + diag * y) % period) / period  # 0..1 внутри периода
    # расстояние до границы полосы (в долях периода), сглаживание ~1 px
    dist = (width / period / 2 - np.abs(t - 0.5)) * period
    k = np.clip(dist + 0.5, 0.0, 1.0)
    return on * k + off * (1 - k)


def ins_plenochnoe() -> Image.Image:
    v = _stripes(64, 32, 1, 0.0, 255.0)
    return Image.fromarray(np.stack([v] * 3, -1).astype(np.uint8))


def ins_unknown() -> Image.Image:
    """Серая штриховка для неизвестного типа."""
    v = _stripes(32, 6, -1, 120.0, 224.0)
    return Image.fromarray(np.stack([v] * 3, -1).astype(np.uint8))


# ------------------------------------------------------------------ сборка

SOIL = {
    "peschanyi": soil_peschanyi,
    "skalnyi": soil_skalnyi,
    "suglinok": soil_suglinok,
    "supes": soil_supes,
    "torf": soil_torf,
    "ilistyi": soil_ilistyi,
}

INSULATION = {
    "bitumnoe": ins_bitumnoe,
    "zavodskoe_pe": ins_zavodskoe,
    "lakokrasochnoe": ins_lakokrasochnoe,
    "plenochnoe": ins_plenochnoe,
    "unknown": ins_unknown,
}


def _save(img: Image.Image | np.ndarray, path: Path) -> None:
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, optimize=True)


def build_textures() -> int:
    n = 0
    for name, fn in SOIL.items():
        _save(fn(), SOIL_DIR / f"{name}.png")
        n += 1
    for name, fn in INSULATION.items():
        _save(fn(), INS_DIR / f"{name}.png")
        n += 1
    # серая штриховка «неизвестный грунт» - та же, что для изоляции
    _save(ins_unknown(), SOIL_DIR / "unknown.png")
    return n + 1


def main() -> int:
    icons = build_icons()
    tiles = build_textures()
    print(f"значков PNG: {icons}; текстур: {tiles}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
