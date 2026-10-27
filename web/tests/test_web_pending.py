"""Книга остаётся прикреплённой, если форма не прошла проверку (ТЗ, 4.2)."""

from __future__ import annotations

import os
import time

from conftest import FORM_DEFAULTS, upload_of
from maps import pending, storage, worker
from maps.models import MapJob


def _pending_files() -> list:
    folder = storage.data_dir() / "pending"
    return list(folder.glob("*")) if folder.exists() else []


def _uploads() -> list:
    folder = storage.uploads_dir()
    return list(folder.glob("*")) if folder.exists() else []


def test_book_stays_attached_after_form_error(client, users, login, small_book, post_new):
    login(users["a"])
    # забыли наименование: форма возвращается, книга отложена
    response = post_new(small_book, object_name="")
    assert response.status_code == 400
    html = response.content.decode()
    assert 'id="attached-book"' in html and "small.xlsx" in html
    assert len(_pending_files()) == 1 and not MapJob.objects.exists()

    # дозаполнили форму без повторного выбора файла
    response = client.post("/maps/new/", dict(FORM_DEFAULTS))
    assert response.status_code == 302
    job = MapJob.objects.get()
    assert job.original_name == "small.xlsx"
    assert storage.resolve(job.source_path).read_bytes() == small_book.read_bytes()
    assert _pending_files() == [] and len(_uploads()) == 1
    assert worker.process_all() == 1
    job.refresh_from_db()
    assert job.state == MapJob.State.CHECKED, job.error_text


def test_attached_book_survives_several_errors(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, object_name="")
    response = client.post("/maps/new/", {**FORM_DEFAULTS, "u_min": "abc"})
    assert response.status_code == 400
    assert 'id="attached-book"' in response.content.decode()
    assert len(_pending_files()) == 1
    assert client.post("/maps/new/", dict(FORM_DEFAULTS)).status_code == 302


def test_new_file_replaces_attached(client, users, login, small_book, post_new, tmp_path):
    login(users["a"])
    post_new(small_book, object_name="")
    other = tmp_path / "other.xlsx"
    other.write_bytes(small_book.read_bytes())
    response = post_new(other)
    assert response.status_code == 302
    assert MapJob.objects.get().original_name == "other.xlsx"
    assert _pending_files() == [] and len(_uploads()) == 1


def test_bad_new_file_drops_attached(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, object_name="")
    bad = upload_of(small_book, name="book.txt")
    response = post_new(bad)
    assert response.status_code == 400
    assert 'id="attached-book"' not in response.content.decode()
    assert _pending_files() == []
    # прежняя книга больше не прикреплена: без файла форма не проходит
    assert client.post("/maps/new/", dict(FORM_DEFAULTS)).status_code == 400
    assert not MapJob.objects.exists()


def test_bad_book_is_not_attached(client, users, login, small_book, post_new):
    login(users["a"])
    response = post_new(upload_of(small_book, name="book.txt"), object_name="")
    assert response.status_code == 400
    assert 'id="attached-book"' not in response.content.decode()
    assert _pending_files() == []


def test_opening_form_again_forgets_attached(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, object_name="")
    html = client.get("/maps/new/").content.decode()
    assert 'id="attached-book"' not in html
    assert _pending_files() == []
    assert client.post("/maps/new/", dict(FORM_DEFAULTS)).status_code == 400


def test_attached_book_is_not_shared_between_users(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, object_name="")
    client.logout()
    login(users["b"])
    response = client.post("/maps/new/", dict(FORM_DEFAULTS))
    assert response.status_code == 400
    assert 'id="attached-book"' not in response.content.decode()
    assert not MapJob.objects.exists()


def test_abandoned_pending_files_are_swept(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, object_name="")
    (old,) = _pending_files()
    stale = time.time() - pending.MAX_AGE_SECONDS - 60
    os.utime(old, (stale, stale))
    client.logout()  # сессия кончилась, файл брошен
    login(users["b"])
    post_new(small_book, object_name="")
    assert old not in _pending_files() and len(_pending_files()) == 1


def test_replace_form_keeps_book_attached(
    client, users, login, small_book, blocking_book, post_new, fake_refinery
):
    login(users["a"])
    assert post_new(blocking_book, utz="on", step="5").status_code == 302
    worker.process_all()
    job = MapJob.objects.get()
    assert job.state == MapJob.State.CHECK_FAILED
    url = f"/maps/{job.pk}/replace/"

    # неверный шаг: форма возвращается, исправленная книга отложена
    response = client.post(url, {"book": upload_of(small_book), "utz": "on", "step": "99"})
    assert response.status_code == 400
    assert 'id="attached-book"' in response.content.decode()

    response = client.post(url, {"utz": "on", "step": "5"})
    assert response.status_code == 302
    job.refresh_from_db()
    assert job.state == MapJob.State.UPLOADED and job.original_name == "small.xlsx"
    assert _pending_files() == []
