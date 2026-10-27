"""Общие заготовки тестов сайта (pytest-django)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from maps import storage, worker
from maps.models import MapJob
from maps.services import params_to_dict
from openpyxl import Workbook
from PIL import Image

from cormap.params import BuildParams
from tests.fake_refinery import fake_refinery  # noqa: F401  (фикстура заглушки refinery)
from tests.synth import make_workbook

PASSWORD = "Karta-2026-test"


@pytest.fixture(autouse=True)
def _hide_real_refinery(monkeypatch):
    """Настоящий модуль заказчика (cormap/refinery.py) в тестах не участвует.

    Тесты работают на заглушке `fake_refinery`; без неё модуль считается отсутствующим.
    """
    monkeypatch.setitem(sys.modules, "cormap.refinery", None)

FORM_DEFAULTS = {
    "object_name": "МГ Тест, участок 1",
    "diameter_mm": "",
    "start_km": "0",
    "u_min": "-0,85",
    "u_max": "-2,50",
    "depth_min": "0,80",
    "res_low": "20",
    "res_high": "50",
    "paper": "A4",
    "legend": "all",
}

# комментарии небольшой тестовой книги: есть нераспознанная пометка
NOTES = [
    (100, "КИП, КИП-1"),
    (1000, "странная пометка"),
    (1500, "река начало, р. Упа"),
    (1900, "река конец"),
    (2500, "кран"),
]


@pytest.fixture(autouse=True)
def data_dir(settings, tmp_path):
    """Каталог данных каждого теста - во временной папке."""
    path = tmp_path / "data"
    path.mkdir()
    settings.CORMAP_DATA_DIR = path
    return path


@pytest.fixture
def users(db):
    User = get_user_model()
    return {
        "a": User.objects.create_user("alice", password=PASSWORD),
        "b": User.objects.create_user("bob", password=PASSWORD),
        "admin": User.objects.create_user("root", password=PASSWORD, is_staff=True),
    }


@pytest.fixture
def login(client):
    def do(user):
        client.force_login(user)
        return client

    return do


@pytest.fixture
def small_book(tmp_path) -> Path:
    """Небольшая книга: 6 км, шаг 10 м, с нераспознанной пометкой."""
    return make_workbook(tmp_path / "small.xlsx", length_m=6000, step_m=10, notes=NOTES)


@pytest.fixture
def blocking_book(tmp_path) -> Path:
    """Книга без обязательной колонки «U т-з выкл, В»."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Result"
    ws.append(["Дистанция, м", "U т-з, В"])
    for i in range(10):
        ws.append([i * 10, -1.2])
    path = tmp_path / "blocking.xlsx"
    wb.save(path)
    return path


def upload_of(path: Path, name: str | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name or path.name, path.read_bytes())


@pytest.fixture
def post_new(client):
    """Отправка формы «Новая карта»: post_new(book_path, **изменения полей)."""

    def do(book: Path | SimpleUploadedFile, **fields):
        data = {**FORM_DEFAULTS, **fields}
        data["book"] = upload_of(book) if isinstance(book, Path) else book
        return client.post("/maps/new/", data)

    return do


@pytest.fixture
def make_job(users, small_book):
    """Карта в базе с настоящей книгой: make_job(owner, check=True) -> MapJob."""

    def do(owner, *, book: Path | None = None, check: bool = False, **fields) -> MapJob:
        src = book or small_book
        rel = storage.new_upload_name()
        target = storage.resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, target)
        params = BuildParams(object_name=fields.pop("object_name", "Тестовая карта"))
        job = MapJob.objects.create(
            owner=owner,
            object_name=params.object_name,
            params=params_to_dict(params),
            original_name=src.name,
            source_path=rel,
            **fields,
        )
        if check:
            worker.process_all()
            job.refresh_from_db()
        return job

    return do


@pytest.fixture
def done_job(make_job):
    """Готовая карта с поддельными файлами результата (быстро, без построения)."""

    def do(owner, **fields) -> MapJob:
        job = make_job(owner, **fields)
        rel = storage.job_results_rel(job.pk)
        root = storage.resolve(rel)
        (root / "png").mkdir(parents=True)
        (root / "karta.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 2000)
        (root / "karta_png.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
        (root / "karta.xlsx").write_bytes(b"PK\x05\x06" + b"\0" * 18)
        for sheet in (1, 2):
            Image.new("RGB", (1200, 800), "white").save(root / "png" / f"list_01_{sheet}.png")
        MapJob.objects.filter(pk=job.pk).update(
            state=MapJob.State.DONE,
            pdf_path=f"{rel}/karta.pdf",
            zip_path=f"{rel}/karta_png.zip",
            xlsx_path=f"{rel}/karta.xlsx",
            png_dir=f"{rel}/png",
            result_info={"sections": 1, "pages": 3, "warnings": [], "warnings_total": 0},
        )
        job.refresh_from_db()
        return job

    return do
