"""Тесты листов PDF и PNG (этап 6)."""

import time
import zipfile

import numpy as np
import pytest
from matplotlib.patches import Polygon, Rectangle
from PIL import Image

from cormap import assets
from cormap.build import BuildError, build_map
from cormap.check import check_workbook
from cormap.cli import main
from cormap.layout import place_objects, split_manual
from cormap.layout_settings import load_settings
from cormap.notes import TrackObject
from cormap.params import BuildParams, ParamsError
from cormap.render_pdf import (
    decimate_minmax,
    defect_objects,
    make_context,
    render_sheet1,
    render_sheet2,
    resistivity_axis,
)
from cormap.render_settings import load_render_settings
from tests.conftest import BASE
from tests.synth import make_sample_like, make_workbook

NAME = "Тестовый объект"


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return make_sample_like(tmp_path_factory.mktemp("s") / "sample.xlsx")


def _setup(path, split="0;5500"):
    survey, report, notes = check_workbook(path)
    assert report.ok
    params = BuildParams(NAME, split=split)
    objects = (*notes.objects, *defect_objects(survey))
    lo, hi = survey.distance_range
    sp = split_manual(lo, hi, params.split, None, objects)
    ctx = make_context(survey, notes.objects, len(sp.sections))
    return survey, notes, params, objects, sp.sections, ctx


# --- параметры ---------------------------------------------------------------


def test_params_defaults_and_errors():
    p = BuildParams(" Объект ", split="0; 3000;5500")
    assert p.object_name == "Объект" and p.split == (0.0, 3000.0, 5500.0)
    assert BuildParams("x", split="2500").split == 2500.0
    assert BuildParams("x", split="авто").split == "auto"
    for kwargs in (
        {"object_name": " "},
        {"object_name": "x", "u_max": -0.5},
        {"object_name": "x", "res_low": 60},
        {"object_name": "x", "paper": "A5"},
        {"object_name": "x", "legend": "много"},
        {"object_name": "x", "split": "abc"},
        {"object_name": "x", "depth_min": 0},
    ):
        with pytest.raises(ParamsError):
            BuildParams(**kwargs)


def test_res_axis_max_param():
    assert BuildParams("x").res_axis_max is None
    assert BuildParams("x", res_axis_max=120).res_axis_max == 120.0
    for value in (50, 30, 0, -5, float("inf"), float("nan")):  # res_high по умолчанию 50
        with pytest.raises(ParamsError) as exc:
            BuildParams("x", res_axis_max=value)
        assert exc.value.field == "res_axis_max"


def test_resistivity_axis_fixed_top():
    w_small = type("W", (), {"inside": True, "y": np.array([5.0, 30.0])})()
    w_big = type("W", (), {"inside": True, "y": np.array([5.0, 900.0])})()
    free = BuildParams("x")
    fixed = BuildParams("x", res_axis_max=130)
    # без параметра - прежнее поведение: верх по данным
    assert resistivity_axis(w_small, free)[0] == 80.0
    assert resistivity_axis(w_big, free)[0] > 900
    # с параметром - ровно заданный верх, данные не влияют
    for w in (w_small, w_big):
        top, step = resistivity_axis(w, fixed)
        assert top == 130.0 and step > 0


def test_build_with_res_axis_max(sample, tmp_path):
    res = build_map(
        sample, BuildParams(NAME, split="5500", res_axis_max=80), tmp_path, png=False, xlsx=False
    )
    assert res.pdf_path.exists() and res.pages == 1 + 2


# --- прореживание ------------------------------------------------------------


def test_decimate_keeps_extrema_order_and_short_series():
    rng = np.random.default_rng(1)
    x = np.arange(10_000.0)
    y = rng.normal(size=x.size)
    xo, yo = decimate_minmax(x, y, 100)
    assert len(xo) < len(x) and np.all(np.diff(xo) > 0)
    assert yo.min() == y.min() and yo.max() == y.max()
    bins = np.minimum((x / x[-1] * 100).astype(int), 99)
    for b in range(100):
        sel = y[bins == b]
        out = yo[np.minimum((xo / x[-1] * 100).astype(int), 99) == b]
        assert out.min() == sel.min() and out.max() == sel.max()
    xs, ys = decimate_minmax(x[:300], y[:300], 100)  # 300 <= 4 * 100
    assert np.array_equal(xs, x[:300]) and np.array_equal(ys, y[:300])


def test_decimate_gaps():
    x = np.arange(2000.0)
    y = [float(i % 7) for i in range(2000)]
    for i in range(900, 1100):
        y[i] = None
    xo, yo = decimate_minmax(x, y, 10)
    nan_idx = np.flatnonzero(np.isnan(yo))
    assert len(nan_idx) == 1  # один разрыв
    assert not np.any((xo[~np.isnan(yo)] >= 900) & (xo[~np.isnan(yo)] < 1100))


# --- геометрия и состав листов -------------------------------------------------


def test_geometry_and_strips(sample):
    survey, notes, params, objects, sections, ctx = _setup(sample, "0;3000;5500")
    ls = load_settings()
    sec = sections[0]
    fig1 = render_sheet1(survey, sec, params, ctx)
    placement = place_objects(objects, sec, ls)
    fig2 = render_sheet2(survey, sec, placement, params, ctx)
    for fig in (fig1, fig2):
        assert fig.get_size_inches()[0] * 25.4 == pytest.approx(297.0, abs=0.01)
        assert fig.get_size_inches()[1] * 25.4 == pytest.approx(210.0, abs=0.01)
    plots1 = [a for a in fig1.axes if a.get_position().width < 0.99]
    plots2 = [a for a in fig2.axes if a.get_position().width < 0.99]
    assert len(plots1) == 5 and len(plots2) == 5
    for fig, axes in ((fig1, plots1), (fig2, plots2)):
        w, _ = fig.get_size_inches() * 25.4
        for ax in axes:
            assert ax.get_position().width * w == pytest.approx(sec.plot_width_mm, abs=0.1)
            left = ax.get_position().x0 * w
            for dist in (sec.start_m, 1234.5, sec.start_m + sec.nominal_length_m):
                px = ax.transData.transform((dist, 0.0))[0] / fig.dpi * 25.4
                assert px - left == pytest.approx(sec.x_mm(dist), abs=0.5)
    # лист 1: подписи делений с запятой и двумя знаками, ось потенциала перевёрнута
    labels = [t.get_text() for t in plots1[0].get_xticklabels()]
    assert labels[0] == "0,00" and labels[1] == "0,50"
    assert plots1[0].yaxis_inverted()
    assert [t.get_text() for t in plots1[0].get_yticklabels()][0] == "-3,00"
    texts = [t.get_text() for t in fig1.axes[0].texts]
    assert NAME in texts


def test_start_km_in_ticks(sample):
    survey, notes, _, objects, sections, ctx = _setup(sample)
    params = BuildParams(NAME, start_km=12.5, split="0;5500")
    fig = render_sheet1(survey, sections[0], params, ctx)
    first = [a for a in fig.axes if a.get_position().width < 0.99][0]
    assert first.get_xticklabels()[0].get_text() == "12,50"


def test_old_template_empty_strips(book):
    path = book(BASE, [[0, -1.5, -1.3], [2, -1.5, -1.3], [4, -1.4, -1.2]])
    survey, report, notes = check_workbook(path)
    assert report.ok
    params = BuildParams(NAME, split="0;4")
    sp = split_manual(0, 4, params.split)
    ctx = make_context(survey, notes.objects, 1)
    sec = sp.sections[0]
    fig1 = render_sheet1(survey, sec, params, ctx)
    fig2 = render_sheet2(survey, sec, place_objects([], sec), params, ctx)
    for fig, n in ((fig1, 2), (fig2, 5)):
        found = [t for a in fig.axes for t in a.texts if t.get_text() == "нет данных"]
        assert len(found) == n


# --- построение --------------------------------------------------------------


def test_build_sample_manual(sample, tmp_path):
    res = build_map(sample, BuildParams(NAME, split="0;3000;5500"), tmp_path)
    assert len(res.sections) == 2
    assert (res.sections[0].start_m, res.sections[0].end_m) == (0.0, 3000.0)
    assert (res.sections[1].start_m, res.sections[1].end_m) == (3000.0, 5500.0)
    assert res.pdf_path.exists() and res.pages == 1 + 2 * 2
    names = sorted(p.name for p in res.png_paths)
    assert names == ["list_01_1.png", "list_01_2.png", "list_02_1.png", "list_02_2.png"]
    with Image.open(res.png_paths[0]) as im:
        assert abs(im.width - 297 / 25.4 * 300) <= 2 and abs(im.height - 210 / 25.4 * 300) <= 2
    with zipfile.ZipFile(res.zip_path) as zf:
        assert sorted(zf.namelist()) == names
    assert "рисование листов" in res.timings


def test_build_legend_modes_and_no_png(sample, tmp_path):
    res = build_map(sample, BuildParams(NAME, split="5500", legend="none"), tmp_path, png=False)
    assert res.pages == 2 and res.png_paths == [] and res.zip_path is None
    res = build_map(
        sample, BuildParams(NAME, split="5500", legend="used"), tmp_path / "u", png=False
    )
    assert res.pages == 3


def test_build_progress_and_auto(sample, tmp_path):
    calls = []
    res = build_map(
        sample, BuildParams(NAME), tmp_path, png=False, progress=lambda d, t: calls.append((d, t))
    )
    assert res.pages == 1 + 2 * len(res.sections)
    assert calls[-1][0] == calls[-1][1] == res.pages


def test_build_blocking_error(book, tmp_path):
    path = book(BASE, [[0, "x", -1]])
    with pytest.raises(BuildError) as exc:
        build_map(path, BuildParams(NAME), tmp_path)
    assert not exc.value.report.ok


# --- командная строка --------------------------------------------------------


def test_cli_build(sample, book, tmp_path, capsys):
    out = tmp_path / "out"
    code = main(
        ["build", str(sample), "--out", str(out), "--name", NAME, "--split", "5500", "--no-png"]
    )
    assert code == 0 and (out / "karta.pdf").exists()
    assert "Карта построена" in capsys.readouterr().out
    bad = book(BASE, [[0, "x", -1]])
    assert main(["build", str(bad), "--out", str(tmp_path / "o2"), "--name", NAME]) == 1


# --- тяжёлые -----------------------------------------------------------------


@pytest.mark.slow
def test_build_50km_under_60s(tmp_path, capsys):
    path = make_workbook(tmp_path / "b.xlsx", length_m=50_000, step_m=2.0)
    t0 = time.perf_counter()
    res = build_map(path, BuildParams(NAME), tmp_path / "out")
    elapsed = time.perf_counter() - t0
    with capsys.disabled():
        print(
            f"\n[slow] 50 км: {elapsed:.1f} с, участков {len(res.sections)}; {res.format_timings()}"
        )
    assert elapsed <= 60


@pytest.mark.slow
def test_build_300km_sparse(tmp_path, capsys):
    notes = [(d, "КИП, КИП") for d in range(0, 300_000, 25_000)]
    path = make_workbook(tmp_path / "b.xlsx", length_m=300_000, step_m=2.0, notes=notes)
    t0 = time.perf_counter()
    res = build_map(path, BuildParams(NAME), tmp_path / "out")
    elapsed = time.perf_counter() - t0
    with capsys.disabled():
        print(
            f"[slow] 300 км: {elapsed:.1f} с, участков {len(res.sections)}; {res.format_timings()}"
        )
    assert len(res.sections) == 3 and res.pdf_path.exists() and len(res.png_paths) == 6
    assert elapsed <= 300


# --- согласование проверки и построения --------------------------------------


def test_check_and_build_same_split(sample, tmp_path):
    from cormap.check import check_and_split

    for split in ("auto", "0;3000;5500"):
        _, _, _, sp = check_and_split(sample, split=split)
        res = build_map(sample, BuildParams(NAME, split=split), tmp_path / split[:2], png=False)
        bounds = lambda s: [(x.start_m, x.end_m, x.nominal_length_m) for x in s.sections]  # noqa: E731
        assert bounds(sp) == bounds(res.split)


def test_defect_issues_in_check_report(book, tmp_path):
    cols = [*BASE, "Дистанция повреждений, м", "Повреждения ИП"]
    path = book(cols, [[0, -1.5, -1.3, 0, "что-то"], [2, -1.5, -1.3, None, None]])
    _, report, _ = check_workbook(path)
    assert report.ok
    assert any(i.code == "defect_class_unknown" for i in report.warnings)
    res = build_map(path, BuildParams(NAME, split="2"), tmp_path, png=False)
    assert any(i.code == "defect_class_unknown" for i in res.report.warnings)


def test_params_ratio_checks_name_field():
    cases = {
        "res_low": {"res_low": 50, "res_high": 50},
        "u_max": {"u_min": -2.5, "u_max": -0.85},
        "depth_min": {"depth_min": 0},
        "diameter_mm": {"diameter_mm": -1},
    }
    for field, kwargs in cases.items():
        with pytest.raises(ParamsError) as exc:
            BuildParams("x", **kwargs)
        assert exc.value.field == field
    assert BuildParams("x", diameter_mm=None).diameter_mm is None


def test_split_text_keeps_precision():
    assert BuildParams("x", split=(0.0, 123456.5, 2500.0)).split_text == "0; 123456.5; 2500"
    assert BuildParams("x", split=2500.0).split_text == "2500"
    assert BuildParams("x", split=1234567.125).split_text == "1234567.125"
    again = BuildParams("x", split=BuildParams("x", split=(0.0, 123456.5)).split_text)
    assert again.split == (0.0, 123456.5)


def test_depth_axis_step_grows_with_wide_range():
    from types import SimpleNamespace

    import numpy as np

    from cormap.render_pdf import depth_axis, potential_axis
    from cormap.render_settings import load_render_settings

    rs = load_render_settings()
    p = BuildParams("x")
    w = SimpleNamespace(inside=True, y=np.array([0.9, 1.0]))
    lo, hi, step = depth_axis(w, p, rs)
    assert step == pytest.approx(0.1) and (hi - lo) / step <= 10
    w = SimpleNamespace(inside=True, y=np.array([0.7, 2.1]))
    lo, hi, step = depth_axis(w, p, rs)
    assert step == pytest.approx(0.2) and (hi - lo) / step <= 10
    w = SimpleNamespace(inside=True, y=np.array([0.7, 6.0]))
    lo, hi, step = depth_axis(w, p, rs)
    assert step >= 0.5 and (hi - lo) / step <= 10
    # потенциал: широкий разброс не даёт больше 10 делений
    wide = SimpleNamespace(inside=True, y=np.array([-9.0, -0.1]))
    top, bottom, step = potential_axis(wide, wide, p, rs)
    assert (bottom - top) / step <= 10 + 1e-9


# --- протяжённые объекты под трубой (ТЗ, 7.1) -------------------------------------

EXT_TYPES = ("water", "swamp", "forest", "ravine", "air_crossing", "open_area")


def _objects_axes(sample, objects, split, index=0):
    """Рисует лист 2 с объектами; возвращает (ось полосы объектов, участок, размещение)."""
    survey, _, params, _, _, ctx = _setup(sample, "0;5500")
    sp = split_manual(0.0, 5500.0, params.split if split is None else split, None, objects)
    sec = sp.sections[index]
    placement = place_objects(objects, sec, load_settings())
    fig = render_sheet2(survey, sec, placement, params, ctx)
    return fig.axes[-1], sec, placement


def _track(key, start, end):
    return TrackObject(key, "", float(start), float(end), 2, None, None)


def _icons_under_pipe(ax):
    """Значки-картинки под трубой (фоновые градиенты имеют zorder 0)."""
    return [im for im in ax.images if im.get_zorder() == 2]


@pytest.mark.parametrize("key", EXT_TYPES)
@pytest.mark.parametrize(
    ("start", "end", "split", "index"),
    [
        (1000, 1010, "0;5500", 0),  # уже наименьшей ширины
        (1000, 2200, "0;5500", 0),  # обычный
        (-100, 6000, "0;5500", 0),  # весь лист
        (1500, 2500, "0;2000;5500", 0),  # начало на этом листе, конец на следующем
        (1500, 2500, "0;2000;5500", 1),  # начало на предыдущем листе
    ],
)
def test_extended_objects_render_in_all_widths(sample, key, start, end, split, index):
    ax, sec, pl = _objects_axes(sample, [_track(key, start, end)], split, index)
    (p,) = pl.objects
    assert p.band_rect is not None
    x0, x1 = ax.get_xlim()
    assert x0 == sec.start_m and x1 == sec.start_m + sec.nominal_length_m
    assert 0 <= p.band_rect.x0 and p.band_rect.x1 <= sec.plot_width_mm + 1e-6


@pytest.mark.parametrize("key", ("swamp", "forest"))
@pytest.mark.parametrize("end", (1005, 1100, 1900))
def test_whole_icons_only(sample, key, end):
    ax, sec, pl = _objects_axes(sample, [_track(key, 1000, end)], "0;5500")
    (p,) = pl.objects
    b = p.band_rect
    w_icon = assets.icon_size_mm(key)[0]
    k = sec.nominal_length_m / sec.plot_width_mm
    icons = _icons_under_pipe(ax)
    assert len(icons) == max(1, int(b.width / w_icon + 1e-9))
    for im in icons:
        e = im.get_extent()
        assert (e[1] - e[0]) / k == pytest.approx(w_icon)  # не обрезан и не сжат
        assert e[0] >= sec.start_m + b.x0 * k - 1e-6
        assert e[1] <= sec.start_m + b.x1 * k + 1e-6


def test_water_has_fill_and_two_shores(sample):
    ax, _, _ = _objects_axes(sample, [_track("water", 1000, 1500)], "0;5500")
    fills = [a for a in ax.patches if isinstance(a, Polygon)]
    assert len(fills) == 1
    rs = load_render_settings()
    shores = [ln for ln in ax.lines if ln.get_linewidth() == pytest.approx(rs.lines.water_shore)]
    assert len(shores) == 2
    assert not _icons_under_pipe(ax)  # плитки значков нет


def test_open_area_single_dashed_frame_with_even_hatch(sample):
    ax, sec, pl = _objects_axes(sample, [_track("open_area", 1000, 2000)], "0;5500")
    rs = load_render_settings()
    (frame,) = [a for a in ax.patches if isinstance(a, Rectangle)]
    assert frame.get_linestyle() != "-"  # пунктирная рамка
    hatch = [ln for ln in ax.lines if ln.get_zorder() == 2.05]
    assert all(ln.get_linewidth() == pytest.approx(rs.lines.open_hatch) for ln in hatch)
    xs = [ln.get_xdata()[0] for ln in hatch]
    assert len(xs) > 3
    steps = np.diff(xs)
    assert np.allclose(steps, steps[0])  # постоянный шаг


def test_air_crossing_has_two_posts(sample):
    ax, _, _ = _objects_axes(sample, [_track("air_crossing", 1000, 2000)], "0;5500")
    rs = load_render_settings()
    posts = [ln for ln in ax.lines if ln.get_linewidth() == pytest.approx(rs.lines.air_post)]
    assert len(posts) == 2


def test_ravine_is_one_picture_over_band(sample):
    ax, sec, pl = _objects_axes(sample, [_track("ravine", 1000, 2000)], "0;5500")
    (im,) = _icons_under_pipe(ax)
    k = sec.nominal_length_m / sec.plot_width_mm
    b = pl.objects[0].band_rect
    assert im.get_extent()[1] - im.get_extent()[0] == pytest.approx(b.width * k)


def test_excel_strip_uses_same_drawer(sample):
    """Excel получает картинку полосы объектов из того же _strip_objects (render_strip_png)."""
    from cormap import render_pdf, render_xlsx

    assert render_xlsx.render_strip_png is render_pdf.render_strip_png


def test_warning_resistivity_above_axis(sample, tmp_path):
    survey, _, _ = check_workbook(sample)
    top = max(survey.soil.resistivity)

    def codes(**kw):
        res = build_map(
            sample, BuildParams(NAME, split="5500", **kw), tmp_path, png=False, xlsx=False
        )
        return [i for i in res.report.warnings if i.code == "res_above_axis"]

    assert top > 60  # в образце УЭС выше верха оси 60
    found = codes(res_axis_max=60)
    assert len(found) == 1 and "наибольшее значение" in found[0].message
    assert codes(res_axis_max=top + 100) == []
    assert codes() == []
