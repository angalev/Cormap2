"""Сериализация результатов проверки (cormap.serialize): JSON и пересчёт разбиения."""

from __future__ import annotations

import json

import pytest

from cormap.check import check_and_split, check_workbook
from cormap.defects import defect_objects
from cormap.layout import LayoutError
from cormap.serialize import (
    describe_record,
    notes_to_dict,
    objects_from_list,
    objects_to_list,
    report_to_dict,
    resplit,
    split_to_dict,
)
from tests.synth import make_workbook

NOTES = [
    (100, "КИП, КИП-1"),
    (1000, "странная пометка"),
    (1500, "река начало, р. Упа"),
    (3000, "кран"),
]


@pytest.fixture
def checked(tmp_path):
    path = make_workbook(tmp_path / "b.xlsx", length_m=6000, step_m=10, notes=NOTES)
    return check_and_split(path)


def test_report_and_notes_are_json(checked):
    survey, report, notes, split = checked
    data = {
        "report": report_to_dict(report),
        "notes": notes_to_dict(notes),
        "split": split_to_dict(split),
        "objects": objects_to_list((*notes.objects, *defect_objects(survey))),
    }
    restored = json.loads(json.dumps(data))
    assert restored == data
    assert restored["report"]["blocks"][0]["rows"] == 601
    assert restored["notes"]["unknown"] == 1
    bad = [r for r in restored["notes"]["records"] if not r["recognized"]]
    assert [r["text"] for r in bad] == ["странная пометка"]
    assert bad[0]["what"] == "НЕ РАСПОЗНАНА"
    assert restored["notes"]["unpaired"] == 1
    assert restored["notes"]["unpaired_issues"][0]["row"] > 1
    assert restored["split"]["count"] == 1
    assert restored["split"]["bounds_m"] == [0.0, 6000.0]


def test_describe_record_matches_format_notes(checked):
    from cormap.notes import format_notes

    _, _, notes, _ = checked
    text = format_notes(notes)
    for rec in notes.records:
        assert describe_record(rec) in text


def test_objects_roundtrip(checked):
    survey, _, notes, _ = checked
    objects = (*notes.objects, *defect_objects(survey))
    assert objects_from_list(json.loads(json.dumps(objects_to_list(objects)))) == objects


def test_resplit_equals_check_and_split(tmp_path):
    path = make_workbook(tmp_path / "b.xlsx", length_m=6000, step_m=10, notes=NOTES)
    survey, _, notes = check_workbook(path)
    objects = (*notes.objects, *defect_objects(survey))
    rng = survey.distance_range
    for spec in ("auto", "2000", "0; 3000; 6000"):
        _, _, _, expected = check_and_split(path, split=spec, start_km=5.0)
        got = resplit(objects, rng, spec, "A4", 5.0)
        assert got.table() == expected.table()
        assert got.issues == expected.issues


def test_resplit_errors_are_layout_errors():
    with pytest.raises(LayoutError):
        resplit((), (0.0, 1000.0), "0; 500; 400")
    with pytest.raises(LayoutError):
        resplit((), (0.0, 1000.0), "abc")


def test_resplit_without_range():
    result = resplit((), None, "auto")
    assert len(result.sections) == 1


def test_split_to_dict_has_conflicts():
    from cormap.layout import split_manual
    from cormap.notes import TrackObject
    from cormap.serialize import split_to_dict

    objs = [TrackObject("kip", f"К{i}", 100.0, None, 2 + i, None) for i in range(3)]
    res = split_manual(0, 1000, 1000, None, objs, start_km=12.0)
    data = split_to_dict(res)
    assert data["conflicts"] and data["conflicts"][0][0] == 1
    assert data["conflicts"][0][3] == 100.0
    assert "на км 12,10" in data["issues"][0]["message"]
