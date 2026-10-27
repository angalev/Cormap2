import pytest

from cormap.check import check_workbook
from cormap.cli import main
from cormap.model import CommentItem
from cormap.notes import (
    END,
    OBJECT_TYPES,
    POINT,
    START,
    get_type,
    parse_comments,
    used_types,
)
from tests.conftest import BASE
from tests.synth import make_workbook


def parse(*texts, start_row=2):
    """Разбор списка текстов: дистанция = 100 * номер, строки подряд."""
    items = [CommentItem(100.0 * (i + 1), start_row + i, t) for i, t in enumerate(texts)]
    return parse_comments(items)


def one(text):
    """Тип единственной пометки (или None)."""
    res = parse(text)
    assert len(res.records) == 1
    return res.records[0].type_key


# --- реестр ----------------------------------------------------------------


def test_registry_is_consistent():
    keys = [t.key for t in OBJECT_TYPES]
    assert len(keys) == len(set(keys))
    icons = sorted(t.icon for t in OBJECT_TYPES if t.icon is not None)
    assert icons == list(range(1, 19))
    for t in OBJECT_TYPES:
        assert t.group in ("legend", "legacy")
        assert all(p == p.lower() and "ё" not in p for p in t.patterns)
        assert (t.icon is None) == (t.group == "legacy")
    assert {t.cross_label for t in OBJECT_TYPES if t.cross_label} == {"К", "Н", "В", "Г"}


# Ключи обеих таблиц 4.3 и старого CorrosionScheme.markers -> тип.
ALL_KEYS = [
    # новая легенда
    ("кип", "kip"),
    ("укз", "ukz"),
    ("удз", "udz"),
    ("кран", "crane"),
    ("кзоу", "kzou"),
    ("кпоу", "kzou"),
    ("изолирующая вставка", "vei"),
    ("фланец", "vei"),
    ("вл", "vl"),
    ("лэп", "vl"),
    ("пересечение с кабелем", "cable"),
    ("пересечение с нефтепроводом", "oil_pipe"),
    ("пересечение с водопроводом", "water_pipe"),
    ("пересечение с газопроводом", "gas_pipe"),
    ("асфальт а/д", "road"),
    ("грунт а/д", "road"),
    ("ж/д", "rail"),
    ("переезд", "crossing"),
    ("река", "water"),
    ("ручей", "water"),
    ("озеро", "water"),
    ("пруд", "water"),
    ("болото", "swamp"),
    ("болотистая местность", "swamp"),
    ("лес", "forest"),
    ("пип", "pip"),
    # старый словарь
    ("пк", "picket"),
    ("свеча", "vent"),
    ("отвод", "bend"),
    ("грс", "grs"),
    ("кс", "ks"),
    ("выход на поверхность", "surface_exit"),
    ("выход", "surface_exit"),
    ("открытый участок", "open_area"),
    ("воздушный переход", "air_crossing"),
    ("кожух", "casing"),
    ("кожух б/с", "casing"),
    ("шурф", "pit"),
    ("поворот влево", "turn_left"),
    ("поворот вправо", "turn_right"),
    ("овраг", "ravine"),
    ("овраг начало", "ravine"),
    ("овраг конец", "ravine"),
    ("кип нерабочий", "kip_nr"),
    ("укз нерабочая", "ukz_nr"),
    ("удз нерабочая", "udz_nr"),
]


@pytest.mark.parametrize(("text", "key"), ALL_KEYS)
def test_every_key_is_recognized(text, key):
    assert one(text) == key
    assert one(text.upper()) == key


def test_every_registry_pattern_is_covered():
    covered = {t for t, _ in ALL_KEYS}
    for t in OBJECT_TYPES:
        for p in t.patterns:
            assert p in covered or any(p in c for c in covered), p
            assert one(p) == t.key


def test_get_type():
    assert get_type("kip").icon == 13
    assert get_type("cable").cross_label == "К"
    with pytest.raises(KeyError):
        get_type("нет такого")


# --- сопоставление -----------------------------------------------------------


def test_longest_key_wins():
    assert one("кип нерабочий") == "kip_nr"
    assert one("КИП нерабочий, КИП-5") == "kip_nr"
    assert one("укз нерабочая") == "ukz_nr"
    assert one("кожух б/с") == "casing"
    assert one("кип") == "kip"


@pytest.mark.parametrize("text", ["вл", "ВЛ", "лэп", "ВЛ-110 кВ", "ВЛ 10кВ", "ЛЭП-35", "вл,x"])
def test_whole_word_positive(text):
    assert one(text) == "vl"


@pytest.mark.parametrize(
    "text", ["влажный грунт", "пкр", "ксения", "грсс", "полэп", "авл", "вкс", "ПКР12", "кст"]
)
def test_whole_word_negative(text):
    assert one(text) is None


def test_whole_word_others():
    assert one("ПК") == "picket"
    assert one("пк 12") == "picket"
    assert one("КС") == "ks"
    assert one("ГРС-2") == "grs"


def test_normalization_case_yo_spaces():
    assert one("  КиП   НЕРАБОЧИЙ ") == "kip_nr"
    assert one("Пересечение   с кабелем") == "cable"
    assert one("ОЗЕРО") == "water"
    assert one("Ёлка") is None
    assert one("ж/Д") == "rail"


def test_label_keeps_original_spelling():
    res = parse("  КИП ,  КИП-14 Ёлка  ")
    rec = res.records[0]
    assert rec.type_key == "kip"
    assert rec.label == "КИП-14 Ёлка"
    assert rec.text == "КИП ,  КИП-14 Ёлка"


def test_label_after_first_comma_only():
    rec = parse("река начало, р. Упа, левый берег").records[0]
    assert rec.label == "р. Упа, левый берег"


def test_multiple_notes_in_cell():
    res = parse("УКЗ, УКЗ-3; КИП;; ; кран")
    assert [r.type_key for r in res.records] == ["ukz", "kip", "crane"]
    assert [r.label for r in res.records] == ["УКЗ-3", "", ""]
    assert [o.type_key for o in res.objects] == ["ukz", "kip", "crane"]
    assert {r.row for r in res.records} == {2}


@pytest.mark.parametrize(
    ("text", "voltage"),
    [
        ("ВЛ-110 кВ", "110 кВ"),
        ("ВЛ 10кВ", "10 кВ"),
        ("ЛЭП-35", "35 кВ"),
        ("вл - 220 КВ", "220 кВ"),
        ("ВЛ-0,4 кВ", "0,4 кВ"),
        ("ВЛ, 110 кВ", "110 кВ"),
        ("ВЛ", None),
        ("ВЛ-110 кВ, Л-5", "110 кВ"),
    ],
)
def test_vl_voltage(text, voltage):
    res = parse(text)
    assert res.objects[0].type_key == "vl"
    assert res.objects[0].voltage == voltage


# --- пары начало / конец -----------------------------------------------------


def test_normal_pair():
    res = parse("река начало, р. Упа", "река конец")
    assert not res.issues
    (obj,) = res.objects
    assert (obj.type_key, obj.start, obj.end) == ("water", 100.0, 200.0)
    assert (obj.row_start, obj.row_end, obj.label) == (2, 3, "р. Упа")
    assert [r.role for r in res.records] == [START, END]


def test_nested_different_types():
    res = parse("лес начало", "река начало", "река конец", "лес конец")
    assert not res.issues
    spans = {o.type_key: (o.start, o.end) for o in res.objects}
    assert spans == {"forest": (100.0, 400.0), "water": (200.0, 300.0)}
    assert [o.type_key for o in res.objects] == ["forest", "water"]  # по start


def test_two_pairs_same_type_in_a_row():
    res = parse("ж/д начало", "ж/д конец", "ж/д начало", "ж/д конец")
    assert not res.issues
    assert [(o.start, o.end) for o in res.objects] == [(100.0, 200.0), (300.0, 400.0)]


def test_start_without_end():
    res = parse("кип", "река начало, Упа", "кран")
    (issue,) = res.issues
    assert issue.code == "note_start_without_end" and issue.row == 3
    assert issue.level == "warning"
    assert res.unpaired == 1
    river = next(o for o in res.objects if o.type_key == "water")
    assert river.end is None  # не теряется, рисуется точкой


def test_end_without_start():
    res = parse("кип", "кран", "река конец")
    (issue,) = res.issues
    assert issue.code == "note_end_without_start" and issue.row == 4
    assert res.unpaired == 1 and len(res.objects) == 3


def test_second_start_leaves_first_unpaired():
    res = parse("река начало", "река начало", "река конец")
    assert [i.code for i in res.issues] == ["note_start_without_end"]
    assert res.issues[0].row == 2
    spans = sorted((o.start, o.end) for o in res.objects)
    assert spans == [(100.0, None), (200.0, 300.0)]


def test_pair_must_be_same_type():
    res = parse("река начало", "лес конец")
    assert sorted(i.code for i in res.issues) == [
        "note_end_without_start",
        "note_start_without_end",
    ]


def test_extended_type_without_words_is_point():
    res = parse("река, Упа", "ж/д", "ВЛ-110 кВ")
    assert not res.issues
    assert all(o.end is None for o in res.objects)
    assert all(r.role == POINT for r in res.records)


def test_non_extended_with_start_word_is_point_with_warning():
    res = parse("кип начало")
    assert res.records[0].type_key == "kip" and res.records[0].role == POINT
    assert [i.code for i in res.issues] == ["note_not_extended"]


def test_pair_inside_one_cell():
    res = parse("река начало; река конец")
    assert not res.issues
    assert (res.objects[0].start, res.objects[0].end) == (100.0, 100.0)


def test_pip_is_manual_defect_source():
    res = parse("ПИП начало", "ПИП конец", "пип")
    assert not res.issues
    assert [o.type_key for o in res.objects] == ["pip", "pip"]
    assert (res.objects[0].start, res.objects[0].end) == (100.0, 200.0)
    assert res.objects[1].end is None
    assert get_type("pip").icon == 18


def test_objects_sorted_and_used_types():
    res = parse("кран", "кип", "кран")
    items = [CommentItem(500.0, 2, "кран"), CommentItem(100.0, 3, "кип")]
    res2 = parse_comments(items)
    assert [o.start for o in res2.objects] == [100.0, 500.0]
    assert [t.key for t in used_types(res.objects)] == ["kip", "crane"]  # порядок реестра


# --- нераспознанные ----------------------------------------------------------


def test_unknown_note_in_records_and_issues():
    res = parse("кип", "загадка, подпись; кран")
    assert [r.type_key for r in res.records] == ["kip", None, "crane"]
    unknown = res.records[1]
    assert unknown.text == "загадка, подпись" and unknown.row == 3
    assert (res.recognized, res.unknown) == (2, 1)
    (issue,) = res.issues
    assert issue.code == "note_unknown" and issue.row == 3 and "загадка" in issue.message
    assert len(res.objects) == 2


def test_empty_type_part_is_unknown():
    res = parse(", подпись")
    assert res.records[0].type_key is None
    assert [i.code for i in res.issues] == ["note_unknown"]


def test_empty_input():
    res = parse_comments([])
    assert res.objects == () and res.records == () and res.issues == ()


# --- проверка книги и CLI ----------------------------------------------------


def test_check_workbook_merges_issues(book):
    path = book(
        [*BASE, "Комментарий"],
        [
            [0, -1.0, -0.8, "КИП, КИП-1"],
            [2, -1.0, -0.8, "непонятно"],
            [4, -1.0, -0.8, "река начало"],
        ],
    )
    survey, report, notes = check_workbook(path)
    assert survey is not None and notes is not None
    assert report.ok  # непарные и нераспознанные не блокируют
    assert sorted(i.code for i in report.issues) == ["note_start_without_end", "note_unknown"]
    assert (notes.recognized, notes.unknown, notes.unpaired) == (2, 1, 1)


def test_check_workbook_unreadable(book):
    path = book(["Дистанция, м"], [[0]])
    survey, report, notes = check_workbook(path)
    assert survey is None and notes is None and not report.ok


def test_cli_prints_recognition_table(capsys, book):
    path = book(
        [*BASE, "Комментарий"],
        [
            [0, -1.0, -0.8, "КИП, КИП-14; ВЛ-110 кВ"],
            [2, -1.0, -0.8, "ерунда"],
            [4, -1.0, -0.8, "река начало, Упа"],
        ],
    )
    assert main(["check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Разбор комментариев" in out
    assert "строка | дистанция, м | пометка | как распознана" in out
    assert "Контрольно-измерительный пункт, подпись «КИП-14»" in out
    assert "Высоковольтная линия, 110 кВ" in out
    assert "НЕ РАСПОЗНАНА" in out
    assert "Распознано: 3, не распознано: 1, непарных: 1" in out
    assert "строка 3: Пометка «ерунда» не распознана" in out
    assert out.index("Разбор комментариев") < out.index("Замечания:")


def test_cli_synth_with_custom_notes(capsys, tmp_path):
    path = make_workbook(
        tmp_path / "s.xlsx",
        length_m=1000,
        notes=[(100, "лес начало"), (300, "лес конец"), (300, "КИП"), (500, "ВЛ-35")],
    )
    survey, report, notes = check_workbook(path)
    assert len(survey.comments.items) == 3
    assert report.ok and not notes.issues
    assert [(o.type_key, o.start, o.end) for o in notes.objects] == [
        ("forest", 100.0, 300.0),
        ("kip", 300.0, None),
        ("vl", 500.0, None),
    ]
    assert main(["check", str(path)]) == 0
    assert "Распознано: 4" in capsys.readouterr().out


def test_synth_empty_notes_and_default(tmp_path):
    p1 = make_workbook(tmp_path / "a.xlsx", length_m=1000, notes=[])
    s1, _, n1 = check_workbook(p1)
    assert s1.comments.items == () and n1.records == ()
    p2 = make_workbook(tmp_path / "b.xlsx", length_m=3000)
    _, _, n2 = check_workbook(p2)
    assert n2.records and not n2.issues
    with pytest.raises(ValueError):
        make_workbook(tmp_path / "c.xlsx", length_m=100, notes=[(500, "кип")])


# --- границы слов и подпись по умолчанию -------------------------------------


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("ВЛ110", "vl"),
        ("ПК12", "picket"),
        ("ПК-12", "picket"),
        ("ПК 12+50", "picket"),
        ("КС5", "ks"),
        ("ГРС-2", "grs"),
        ("крановый узел", "crane"),
        ("болотистая местность", "swamp"),
        ("лесополоса", "forest"),
        ("КИП-14", "kip"),
        ("УКЗ№3", "ukz"),
        ("газопровод-отвод", "bend"),
        ("Пересечение с газопроводом", "gas_pipe"),
    ],
)
def test_word_boundaries_positive(text, key):
    assert one(text) == key


@pytest.mark.parametrize(
    "text", ["водоотвод", "пролесок", "скип", "вкс", "пкр", "влажный грунт", "Прокип"]
)
def test_word_boundaries_negative(text):
    assert one(text) is None


def test_vl_without_separator_voltage():
    assert parse("ВЛ110").objects[0].voltage == "110 кВ"
    assert parse("ВЛ110 кВ").objects[0].voltage == "110 кВ"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("КИП-14", "КИП-14"),
        ("УКЗ №3", "УКЗ №3"),
        ("ПК 12+50", "ПК 12+50"),
        ("КС-5", "КС-5"),
        ("ГРС-2", "ГРС-2"),
        ("КИП 7 начало", "КИП 7"),
        ("крановый узел 12", ""),
        ("болотистая местность", ""),
        ("КИП", ""),
        ("КИП, КИП-3", "КИП-3"),
        ("ВЛ-110 кВ", ""),
        ("ВЛ110", ""),
    ],
)
def test_default_label(text, label):
    assert parse(text).records[0].label == label


def test_default_label_excludes_role_words():
    rec = parse("река начало 5").records[0]
    assert rec.role == START and rec.label == "река 5"
    rec = parse("река 5 конец").records[0]
    assert rec.role == END and rec.label == "река 5"
