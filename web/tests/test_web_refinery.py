"""Предварительная обработка модулем refinery на сайте (ТЗ, 4.5), на заглушке модуля."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from conftest import upload_of
from django.core.exceptions import ImproperlyConfigured
from maps import storage, worker
from maps.models import AuditLog, MapJob
from maps.services import params_from_job
from openpyxl import load_workbook

from tests.synth import make_workbook

State = MapJob.State


@pytest.fixture
def template_book(tmp_path):
    """Книга только с листом Template (листа Result нет): без модуля проверка не пройдёт."""
    return make_workbook(tmp_path / "tpl.xlsx", length_m=1000, step_m=10, sheet="Template")


def _page(response) -> str:
    return response.content.decode()


def _upload(post_new, book, **fields):
    fields.setdefault("utz", "on")
    fields.setdefault("step", "5")
    return post_new(book, **fields)


# --- форма ----------------------------------------------------------------------------------


def test_form_has_block_when_enabled(client, users, login, fake_refinery):
    login(users["a"])
    page = _page(client.get("/maps/new/"))
    assert "Обработка данных" in page
    for label in (
        "U т-з вкл",
        "U т-з выкл (генератор)",
        "Левый градиент выкл (0.8 от градиента вкл)",
    ):
        assert label in page
    for title in ("Левый градиент", "Правый градиент", "Интерполяция"):
        assert title in page
    assert 'name="step"' in page and 'value="5"' in page


def test_form_no_block_when_off(client, users, login, fake_refinery, settings):
    settings.CORMAP_REFINERY = "off"
    login(users["a"])
    assert "Обработка данных" not in _page(client.get("/maps/new/"))


def test_form_no_block_without_module(client, users, login, monkeypatch):
    monkeypatch.setitem(sys.modules, "refinery", None)
    login(users["a"])
    assert "Обработка данных" not in _page(client.get("/maps/new/"))


def test_upload_without_block_keeps_params_clean(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book)
    job = MapJob.objects.get()
    assert "refinery" not in job.params


def test_rules_violation_shows_error_and_creates_nothing(
    client, users, login, fake_refinery, template_book, post_new
):
    login(users["a"])
    response = post_new(template_book, utzdc="on", step="5")  # utzdc без utz
    assert response.status_code == 400
    assert "возможно только вместе" in _page(response)
    assert not MapJob.objects.exists()
    response = post_new(template_book, utz="on", step="11")
    assert response.status_code == 400
    assert "от 1 до 10" in _page(response)
    assert not MapJob.objects.exists() and fake_refinery.calls == []


# --- загрузка и проверка ----------------------------------------------------------------------


def test_no_flags_skips_module(client, users, login, fake_refinery, small_book, post_new):
    """Без флажков модуль не вызывается; книга только с листом Result тоже принимается."""
    login(users["a"])
    assert post_new(small_book, step="5").status_code == 302
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECKED, job.error_text
    assert fake_refinery.calls == [] and job.processed_path == ""
    assert "refinery" not in job.check_result
    page = _page(client.get(f"/maps/{job.pk}/"))
    assert "Обработка данных" not in page


def test_no_flags_reads_template_sheet(
    client, users, login, fake_refinery, template_book, post_new
):
    """Без флажков читается лист Template, и в выгрузке Excel лист данных зовётся так же."""
    login(users["a"])
    assert post_new(template_book, step="5").status_code == 302
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECKED, job.error_text
    assert fake_refinery.calls == [] and job.processed_path == ""
    assert job.check_result["sheet"] == "Template"

    assert client.post(f"/maps/{job.pk}/build/").status_code == 302
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.DONE, job.error_text
    wb = load_workbook(storage.resolve(job.xlsx_path), read_only=True)
    assert wb.sheetnames[0] == "Template" and "Данные" not in wb.sheetnames
    wb.close()


def test_no_flags_template_wins_over_result(
    client, users, login, fake_refinery, post_new, tmp_path
):
    """В книге оба листа: без флажков берётся Template."""
    book = make_workbook(tmp_path / "both.xlsx", length_m=1000, step_m=10, sheet="Template")
    wb = load_workbook(book)
    extra = wb.create_sheet("Result")
    extra.append(["Дистанция, м", "U т-з, В", "U т-з выкл, В"])
    extra.append([0, -1.0, -1.0])
    extra.append([5000, -1.0, -1.0])
    wb.save(book)
    login(users["a"])
    post_new(book, step="5")
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECKED, job.error_text
    assert job.check_result["sheet"] == "Template"
    assert job.check_result["dist_range"] == [0.0, 1000.0]


def test_no_flags_and_no_data_sheet_fails_check(
    client, users, login, fake_refinery, post_new, tmp_path
):
    book = make_workbook(tmp_path / "other.xlsx", length_m=1000, step_m=10, sheet="Лист1")
    login(users["a"])
    assert post_new(book, step="5").status_code == 302
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECK_FAILED and fake_refinery.calls == []
    assert "Template" in job.error_text


def test_with_flags_data_sheet_is_result(
    client, users, login, fake_refinery, template_book, post_new
):
    """С флажками книга идёт через модуль: в выгрузке лист данных - Result."""
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECKED, job.error_text
    assert job.check_result["sheet"] == "Result" and len(fake_refinery.calls) == 1


def test_upload_processed_and_checked(client, users, login, fake_refinery, template_book, post_new):
    login(users["a"])
    before = template_book.read_bytes()
    _upload(post_new, template_book, grlu="on", grldc="on", step="4")
    job = MapJob.objects.get()
    saved = job.params["refinery"]
    assert saved["utz"] and saved["grlu"] and saved["grldc"] and saved["step"] == 4
    assert isinstance(saved["random_seed"], int)
    params_from_job(job)  # ключ refinery не мешает параметрам построения

    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.CHECKED, job.error_text
    assert job.processed_path and job.processed_path != job.source_path
    assert storage.resolve(job.processed_path).is_file()
    assert storage.resolve(job.source_path).read_bytes() == before  # исходная нетронута
    (call,) = fake_refinery.calls
    assert call["settings"].random_seed == saved["random_seed"] and call["settings"].step == 4

    info = job.check_result["refinery"]
    assert info["ok"] and info["step"] == 4 and info["seed"] == saved["random_seed"]
    assert set(info["flags"]) == {"utz", "grlu", "grldc"}
    assert info["stats"]["input_rows"] == 101

    page = _page(client.get(f"/maps/{job.pk}/"))
    assert "Обработка данных" in page and "Левый градиент выкл (0.8 от градиента вкл)" in page
    assert "Строк на входе" in page and "Диапазон дистанций" in page

    entry = AuditLog.objects.filter(action=AuditLog.Action.CHECK, job=job).get()
    assert "utz" in entry.details and "шаг 4" in entry.details
    assert f"сид {saved['random_seed']}" in entry.details and "101" in entry.details


def test_book_without_module_is_not_processed(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book)
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECKED and job.processed_path == ""
    assert "Обработка данных" not in _page(client.get(f"/maps/{job.pk}/"))


def test_module_error_stops_job(client, users, login, fake_refinery, template_book, post_new):
    fake_refinery.mode = "fail"
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECK_FAILED
    assert "Ошибка обработки данных [bad_input]" in job.error_text
    assert job.processed_path == ""
    page = _page(client.get(f"/maps/{job.pk}/"))
    assert "[bad_input]" in page
    entry = AuditLog.objects.filter(action=AuditLog.Action.CHECK, job=job).get()
    assert "[bad_input]" in entry.details and "utz" in entry.details


def test_module_exception_stops_job(client, users, login, fake_refinery, template_book, post_new):
    fake_refinery.mode = "raise"
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECK_FAILED
    assert "[calculation_error]" in job.error_text and "Traceback" not in job.error_text


def test_required_without_module(client, users, login, settings, monkeypatch, small_book, post_new):
    settings.CORMAP_REFINERY = "required"
    monkeypatch.setitem(sys.modules, "refinery", None)
    login(users["a"])
    assert "Обработка данных" in _page(client.get("/maps/new/"))
    _upload(post_new, small_book)
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == State.CHECK_FAILED
    assert "[not_installed]" in job.error_text


def _load_settings_copy():
    """Свежая копия модуля настроек (рабочие настройки теста не затрагиваются)."""
    path = Path(__file__).resolve().parents[1] / "config" / "settings.py"
    spec = importlib.util.spec_from_file_location("settings_copy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refinery_setting_from_env(monkeypatch):
    monkeypatch.delenv("CORMAP_REFINERY", raising=False)
    assert _load_settings_copy().CORMAP_REFINERY == "auto"
    for value in ("auto", "required", "off"):
        monkeypatch.setenv("CORMAP_REFINERY", value)
        assert _load_settings_copy().CORMAP_REFINERY == value
    monkeypatch.setenv("CORMAP_REFINERY", "maybe")
    with pytest.raises(ImproperlyConfigured):
        _load_settings_copy()


# --- разбиение, перестроение, замена, удаление ------------------------------------------------


def test_split_and_rebuild_do_not_call_module(
    client, users, login, fake_refinery, template_book, post_new
):
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    processed = job.processed_path
    assert len(fake_refinery.calls) == 1

    response = client.post(f"/maps/{job.pk}/split/", {"mode": "bounds", "value": "0; 0,5; 1"})
    assert response.status_code == 302
    job.refresh_from_db()
    assert job.params["refinery"]["utz"] is True  # настройки модуля пережили смену разбиения
    client.post(f"/maps/{job.pk}/build/")
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.DONE, job.error_text
    client.post(f"/maps/{job.pk}/rebuild/")
    job.refresh_from_db()
    assert job.state == State.CHECKED
    client.post(f"/maps/{job.pk}/build/")
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.DONE
    assert len(fake_refinery.calls) == 1 and job.processed_path == processed


def test_recheck_reuses_processed_book(
    client, users, login, fake_refinery, template_book, post_new
):
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    MapJob.objects.filter(pk=job.pk).update(state=State.UPLOADED)  # как после аварии обработчика
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.CHECKED and len(fake_refinery.calls) == 1
    assert job.check_result["refinery"]["stats"]["input_rows"] == 101


def test_replace_calls_module_again(client, users, login, fake_refinery, template_book, post_new):
    login(users["a"])
    _upload(post_new, template_book, grru="on")
    worker.process_all()
    job = MapJob.objects.get()
    old_processed = storage.resolve(job.processed_path)
    old_seed = job.params["refinery"]["random_seed"]

    page = _page(client.get(f"/maps/{job.pk}/replace/"))
    assert "Обработка данных" in page
    assert re.search(r'name="grru"[^>]*checked', page)  # по умолчанию - настройки карты
    assert not re.search(r'name="grld"[^>]*checked', page)

    response = client.post(
        f"/maps/{job.pk}/replace/",
        {"book": upload_of(template_book), "utz": "on", "utzd": "on", "step": "2"},
    )
    assert response.status_code == 302
    job.refresh_from_db()
    assert job.state == State.UPLOADED and job.processed_path == ""
    assert not old_processed.exists()
    assert job.params["refinery"]["utzd"] is True and job.params["refinery"]["step"] == 2
    assert job.params["refinery"]["grru"] is False
    assert isinstance(job.params["refinery"]["random_seed"], int)
    assert old_seed is not None

    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.CHECKED
    assert len(fake_refinery.calls) == 2
    assert fake_refinery.calls[1]["settings"].utzd is True
    assert storage.resolve(job.processed_path).is_file()


def test_replace_rule_violation(client, users, login, fake_refinery, template_book, post_new):
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    response = client.post(
        f"/maps/{job.pk}/replace/", {"book": upload_of(template_book), "grldc": "on", "step": "5"}
    )
    assert response.status_code == 400
    assert "возможно только при" in _page(response)
    job.refresh_from_db()
    assert job.state == State.CHECKED and len(fake_refinery.calls) == 1


def test_delete_removes_processed_book(
    client, users, login, fake_refinery, template_book, post_new
):
    login(users["a"])
    _upload(post_new, template_book)
    worker.process_all()
    job = MapJob.objects.get()
    source = storage.resolve(job.source_path)
    processed = storage.resolve(job.processed_path)
    assert source.is_file() and processed.is_file()
    client.post(f"/maps/{job.pk}/delete/")
    assert not source.exists() and not processed.exists()
