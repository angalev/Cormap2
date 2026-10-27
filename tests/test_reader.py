import time

import pytest
from openpyxl import load_workbook

from cormap.cli import main
from cormap.model import type_intervals
from cormap.reader import read_workbook
from tests.conftest import BASE, REFERENCE
from tests.synth import make_workbook

# --- эталонные книги -------------------------------------------------------


@pytest.fixture
def five_points(tmp_path):
    """Лист `Template` из test_data.xlsx (5 точек), скопированный в книгу под именем `Result`."""
    wb = load_workbook(REFERENCE / "test_data.xlsx")
    del wb["Result"]
    wb["Template"].title = "Result"
    path = tmp_path / "five.xlsx"
    wb.save(path)
    return path


def test_template_book_without_result_sheet_is_no_sheet():
    survey, report = read_workbook(REFERENCE / "template.xlsx")
    assert survey is None and not report.ok
    assert [i.code for i in report.errors] == ["no_sheet"]
    assert "«Result»" in report.errors[0].message and "Template" in report.errors[0].message


def test_five_points_sheet(five_points):
    survey, report = read_workbook(five_points)
    assert report.ok and not report.issues
    assert report.sheet == "Result" and survey.sheet_name == "Result"
    assert survey.intensive.dist == (0, 1000, 2500, 4000, 5000)
    assert survey.intensive.u_on[0] == -1.5
    assert len(survey.comments.items) == 5
    assert survey.comments.items[0].row == 2 and survey.comments.items[0].text == "Старт"
    assert survey.gps.items[1].text == "55.7568;37.6273"
    assert survey.soil.dist == () and survey.insulation.types == ()


def test_test_data_reads_result_sheet_only():
    survey, report = read_workbook(REFERENCE / "test_data.xlsx")
    assert report.sheet == "Result" and survey.sheet_name == "Result"
    assert len(survey.intensive.dist) == 2501


def test_test_data_result_sheet():
    survey, report = read_workbook(REFERENCE / "test_data.xlsx")
    # «U т-з выкл» на этом листе пуста полностью: предупреждение, не ошибка.
    assert report.ok
    assert [i.code for i in report.issues] == ["required_column_empty"]
    assert "U т-з выкл" in report.issues[0].message
    assert len(survey.intensive.dist) == 2501
    assert survey.distance_range == (0, 5000)
    assert all(v is None for v in survey.intensive.u_off)
    assert len(survey.gradients.dist) == 2501
    assert len(survey.comments.items) == 5
    summary = {b.name: b for b in report.blocks}
    assert summary["Интенсивные измерения"].step == 2
    assert summary["Грунт"].rows == 0
    assert "нет данных" in report.format_text()


def test_old_template_without_new_columns_is_fine():
    survey, report = read_workbook(REFERENCE / "test_data.xlsx")
    assert report.ok
    assert survey.soil.types == () and survey.insulation.types == ()


# --- структура листа и колонок ---------------------------------------------


def test_missing_required_column(book):
    path = book(["Дистанция, м", "U т-з, В"], [[0, -1.0]])
    survey, report = read_workbook(path)
    assert survey is None and not report.ok
    assert "U т-з выкл, В" in report.errors[0].message


def test_columns_any_order_and_normalized_headers(book):
    cols = ["  u т-з ВЫКЛ,  В", "Дистанция, м", "u Т-З, в", "Комментарий"]
    path = book(cols, [[-1.1, 0, -1.4, "КИП"], [-1.2, 2, -1.5, None]])
    survey, report = read_workbook(path)
    assert report.ok, report.format_text()
    assert survey.intensive.u_off == (-1.1, -1.2)
    assert survey.intensive.u_on == (-1.4, -1.5)
    assert survey.comments.items[0].dist == 0


def test_blank_comment_is_not_a_comment(book):
    path = book(BASE + ["Комментарий"], [[0, -1, -1, "Река"], [1, -1, -1, "  "]])
    survey, _ = read_workbook(path)
    assert len(survey.comments.items) == 1


@pytest.mark.parametrize("title", ["result", " RESULT ", "ReSuLt"])
def test_sheet_name_case_and_spaces_insensitive(book, title):
    path = book(BASE, [[0, -1, -1]], sheet=title)
    survey, report = read_workbook(path)
    assert report.ok, report.format_text()
    assert survey.sheet_name == title


def test_result_read_even_if_other_sheet_fits_before_it(book):
    path = book(
        BASE, [[0, -1, -1]], sheet="Данные", extra={"Result": (BASE, [[0, -2, -2], [2, -3, -3]])}
    )
    survey, report = read_workbook(path)
    assert report.ok and report.sheet == "Result"
    assert survey.intensive.dist == (0, 2)


def test_no_result_sheet_is_no_sheet(book):
    path = book(BASE, [[0, -1, -1]], sheet="Данные")
    survey, report = read_workbook(path)
    assert survey is None
    assert [i.code for i in report.errors] == ["no_sheet"]
    assert "Данные" in report.errors[0].message


def test_result_sheet_without_required_columns(book):
    path = book(["A"], [[1]], sheet="Result", extra={"Данные": (BASE, [[0, -1, -1]])})
    survey, report = read_workbook(path)
    assert survey is None and report.errors[0].code == "missing_columns"
    for col in BASE:
        assert col in report.errors[0].message


# --- числа и ошибки ---------------------------------------------------------


def test_decimal_comma_strings(book):
    path = book(BASE, [["0", "-1,25", " -1,1 "], ["2,5", -1.3, "-1,2"]])
    survey, report = read_workbook(path)
    assert report.ok and not report.issues
    assert survey.intensive.dist == (0.0, 2.5)
    assert survey.intensive.u_on == (-1.25, -1.3)


def test_non_numeric_required_is_error_with_address(book):
    path = book(BASE, [[0, -1, -1], [2, "abc", -1], [4, -1, -1]])
    survey, report = read_workbook(path)
    assert not report.ok
    err = report.errors[0]
    assert err.cell == "B3" and err.row == 3 and err.code == "not_a_number"
    assert survey.intensive.u_on == (-1, None, -1)


def test_non_numeric_distance_is_error(book):
    path = book(BASE, [[0, -1, -1], ["x", -1, -1]])
    _, report = read_workbook(path)
    assert report.errors[0].cell == "A3"


def test_non_numeric_optional_is_warning(book):
    cols = BASE + ["Гр-нт лев. вкл, мВ", "Глубина заложения трубы, м", "Дистанция глубины, м"]
    path = book(cols, [[0, -1, -1, "много", "?", 0], [2, -1, -1, 5, 1.5, 2]])
    survey, report = read_workbook(path)
    assert report.ok
    assert sorted(i.cell for i in report.warnings) == ["D2", "E2"]
    assert survey.gradients.left_on == (5,)
    assert survey.depth.depth == (1.5,)


def test_decreasing_distance_has_address(book):
    path = book(BASE, [[0, -1, -1], [10, -1, -1], [4, -1, -1]])
    _, report = read_workbook(path)
    assert not report.ok
    assert report.errors[0].code == "distance_decreases" and report.errors[0].cell == "A4"


def test_decreasing_distance_in_optional_block_is_warning(book):
    cols = BASE + ["Дистанция глубины, м", "Глубина заложения трубы, м"]
    path = book(cols, [[0, -1, -1, 10, 1], [2, -1, -1, 5, 1]])
    _, report = read_workbook(path)
    assert report.ok
    assert report.warnings[0].cell == "D3"


def test_empty_value_with_missing_distance(book):
    path = book(BASE, [[0, -1, -1], [None, -1, -1]])
    _, report = read_workbook(path)
    assert report.errors[0].code == "empty_value" and report.errors[0].cell == "A3"


# --- типы грунта и изоляции, блоки ------------------------------------------


def test_unknown_soil_and_insulation_are_warnings_and_kept(book):
    cols = BASE + ["Дистанция грун, м", "Тип грунта", "Дистанция изоляции, м", "Тип изоляции"]
    rows = [
        [0, -1, -1, 0, "ПЕСЧАНЫЙ", 0, "Плёночное  полимерное"],
        [2, -1, -1, 100, "глина", 100, "ржавчина"],
    ]
    survey, report = read_workbook(book(cols, rows))
    assert report.ok
    assert sorted(i.cell for i in report.warnings) == ["E3", "G3"]
    assert [(t.value, t.known) for t in survey.soil.types] == [
        ("песчаный", True),
        ("глина", False),
    ]
    assert [(t.value, t.known) for t in survey.insulation.types] == [
        ("пленочное полимерное", True),
        ("ржавчина", False),
    ]


def test_empty_optional_blocks_are_not_errors(book):
    cols = BASE + ["Дистанция грун, м", "Уд. соп-е грунтов, Ом*м", "Тип грунта"]
    survey, report = read_workbook(book(cols, [[0, -1, -1, None, None, None]]))
    assert report.ok and not report.issues
    assert survey.soil.dist == ()
    assert {b.name: b.rows for b in report.blocks}["Грунт"] == 0


def test_block_without_distance_column_warns(book):
    cols = BASE + ["Уд. соп-е грунтов, Ом*м"]
    _, report = read_workbook(book(cols, [[0, -1, -1, 50]]))
    assert report.ok
    assert report.warnings[0].code == "block_no_distance"


def test_blocks_have_independent_distances(book):
    cols = BASE + ["Дистанция грун, м", "Уд. соп-е грунтов, Ом*м"]
    rows = [[0, -1, -1, 0, 30], [2, -1, -1, None, None], [4, -1, -1, 100, 40]]
    survey, report = read_workbook(book(cols, rows))
    assert report.ok
    assert survey.intensive.dist == (0, 2, 4)
    assert survey.soil.dist == (0, 100) and survey.soil.resistivity == (30, 40)


def test_trailing_blank_rows_ignored(book):
    path = book(BASE, [[0, -1, -1], [2, -1, -1], [None, None, None], ["", " ", None]])
    survey, report = read_workbook(path)
    assert report.ok and len(survey.intensive.dist) == 2


# --- безопасность и лимиты -------------------------------------------------


def test_not_xlsx(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("x")
    survey, report = read_workbook(p)
    assert survey is None and report.errors[0].code == "bad_extension"


def test_broken_xlsx(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_text("не книга")
    survey, report = read_workbook(p)
    assert survey is None and report.errors[0].code == "bad_file"


def test_missing_file(tmp_path):
    survey, report = read_workbook(tmp_path / "нет.xlsx")
    assert survey is None and report.errors[0].code == "not_found"


def test_row_limit(book):
    path = book(BASE, [[i, -1, -1] for i in range(10)])
    survey, report = read_workbook(path, max_rows=5)
    assert survey is None and report.errors[0].code == "too_many_rows"
    survey, report = read_workbook(path, max_rows=10)
    assert survey is not None and report.ok


def test_size_limit(book):
    path = book(BASE, [[0, -1, -1]])
    survey, report = read_workbook(path, max_bytes=100)
    assert survey is None and report.errors[0].code == "file_too_big"


# --- интервалы типа ---------------------------------------------------------


def test_type_intervals_basic():
    marks = [(1000.0, "торф"), (400.0, "песчаный"), (3000.0, "супесь")]
    assert type_intervals(marks, 0, 5000) == [
        (0, 1000, "песчаный"),  # первая тянется к началу трассы
        (1000, 3000, "торф"),
        (3000, 5000, "супесь"),  # последняя тянется к концу
    ]


def test_type_intervals_edge_cases():
    assert type_intervals([], 0, 100) == []
    assert type_intervals([(5, "торф")], 0, 100) == [(0, 100, "торф")]
    assert type_intervals([(5, "торф")], 50, 50) == []
    # диапазон внутри трассы: действует отметка, начавшаяся раньше диапазона
    marks = [(0, "торф"), (100, "песчаный"), (200, "скальный")]
    assert type_intervals(marks, 120, 150) == [(120, 150, "песчаный")]
    # склейка одинаковых соседних; при одной дистанции побеждает последняя
    assert type_intervals([(0, "торф"), (10, "торф"), (20, "супесь")], 0, 30) == [
        (0, 20, "торф"),
        (20, 30, "супесь"),
    ]
    assert type_intervals([(0, "торф"), (0, "супесь")], 0, 10) == [(0, 10, "супесь")]


# --- синтетическая книга, скорость, CLI --------------------------------------


def test_synth_is_deterministic(tmp_path):
    a = make_workbook(tmp_path / "a.xlsx", length_m=2000, seed=7)
    b = make_workbook(tmp_path / "b.xlsx", length_m=2000, seed=7)
    assert read_workbook(a)[0] == read_workbook(b)[0]


def test_synth_all_blocks_filled(tmp_path):
    path = make_workbook(tmp_path / "s.xlsx", length_m=20_000)
    survey, report = read_workbook(path)
    assert report.ok and not report.issues, report.format_text()
    assert len(survey.intensive.dist) == 10_001
    assert survey.soil.dist and survey.soil.types and survey.depth.dist
    assert survey.defects.dist and survey.insulation.types and survey.comments.items
    assert survey.gradients.dist and survey.gps.items


def test_read_speed_25001_rows(tmp_path):
    path = make_workbook(tmp_path / "big.xlsx")  # 50 км, шаг 2 м
    start = time.perf_counter()
    survey, report = read_workbook(path)
    elapsed = time.perf_counter() - start
    assert report.ok
    assert len(survey.intensive.dist) == 25_001
    assert elapsed < 10, elapsed


def test_cli_exit_codes(capsys, book):
    good = book(BASE, [[0, -1, -1]], name="good.xlsx")
    bad = book(BASE, [[0, "x", -1]], name="bad.xlsx")
    assert main(["check", str(good)]) == 0
    assert main(["check", str(bad)]) == 1
    assert "B2" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:  # build требует --out и --name
        main(["build", str(good)])
    assert exc.value.code == 2


def test_required_column_empty_is_warning(book):
    path = book(BASE, [[0, -1.0, None], [2, -1.1, None]])
    survey, report = read_workbook(path)
    assert survey is not None and report.ok
    issue = report.issues[0]
    assert issue.code == "required_column_empty" and issue.level == "warning"
    assert issue.cell == "C1" and "U т-з выкл, В" in issue.message


def test_required_column_filled_no_warning(book):
    path = book(BASE, [[0, -1.0, -0.8]])
    _, report = read_workbook(path)
    assert not report.issues


# --- лимиты и длина трассы (ТЗ 0.4) -----------------------------------------


def test_default_limits():
    from cormap import reader

    assert reader.MAX_DATA_ROWS == 400_000
    assert reader.MAX_FILE_BYTES == 50 * 1024 * 1024


def test_route_too_long_warning(book):
    long_path = book(BASE, [[0, -1, -1], [300_001, -1, -1]])
    survey, report = read_workbook(long_path)
    assert survey is not None and report.ok
    assert "route_too_long" in [w.code for w in report.warnings]


def test_route_300km_no_warning(book):
    path = book(BASE, [[0, -1, -1], [300_000, -1, -1]])
    _, report = read_workbook(path)
    assert "route_too_long" not in [w.code for w in report.warnings]
