"""Чужие карты и файлы недоступны (ТЗ, приёмка п. 6); администратор видит все."""

from __future__ import annotations

import pytest
from maps import storage
from maps.models import MapJob


def _pages(pk: int) -> list[str]:
    base = f"/maps/{pk}"
    return [
        f"{base}/",
        f"{base}/status/",
        f"{base}/replace/",
        f"{base}/delete/",
        f"{base}/files/pdf/",
        f"{base}/files/png-zip/",
        f"{base}/files/xlsx/",
        f"{base}/preview/1/1/",
        f"{base}/preview/1/2/",
        f"{base}/preview/1/1/?full=1",
    ]


def test_other_user_gets_404_everywhere(client, users, login, done_job):
    job = done_job(users["a"])
    login(users["b"])
    for url in _pages(job.pk):
        assert client.get(url).status_code == 404, url
    for action in ("split", "build", "cancel", "rebuild", "replace", "delete"):
        assert client.post(f"/maps/{job.pk}/{action}/").status_code == 404, action
    assert MapJob.objects.filter(pk=job.pk).exists()
    assert storage.resolve(job.pdf_path).is_file()


def test_owner_gets_files(client, users, login, done_job):
    job = done_job(users["a"])
    login(users["a"])
    pdf = client.get(f"/maps/{job.pk}/files/pdf/")
    assert pdf.status_code == 200
    assert pdf["Content-Type"] == "application/pdf"
    assert "attachment" in pdf["Content-Disposition"]
    assert b"".join(pdf.streaming_content).startswith(b"%PDF")
    assert client.get(f"/maps/{job.pk}/files/png-zip/").status_code == 200
    assert client.get(f"/maps/{job.pk}/files/xlsx/").status_code == 200
    png = client.get(f"/maps/{job.pk}/preview/1/1/")
    assert png.status_code == 200
    assert png["Content-Type"] == "image/png"
    assert b"".join(png.streaming_content).startswith(b"\x89PNG")


def test_admin_sees_all(client, users, login, done_job):
    job = done_job(users["a"])
    login(users["admin"])
    for url in _pages(job.pk):
        if url.endswith(("/replace/", "/delete/")):
            continue  # менять и удалять чужие карты на сайте нельзя
        assert client.get(url).status_code == 200, url
    page = client.get("/")
    assert "Владелец" in page.content.decode()
    assert job.object_name in page.content.decode()
    # менять чужую карту на сайте администратор не может
    assert client.post(f"/maps/{job.pk}/delete/").status_code == 404
    assert client.post(f"/maps/{job.pk}/cancel/").status_code == 404


def test_list_shows_only_own_and_no_owner_column(client, users, login, make_job):
    mine = make_job(users["a"], object_name="Моя карта")
    other = make_job(users["b"], object_name="Чужая карта")
    login(users["a"])
    text = client.get("/").content.decode()
    assert mine.object_name in text
    assert other.object_name not in text
    assert "Владелец" not in text


def test_files_only_when_done(client, users, login, make_job):
    job = make_job(users["a"])
    login(users["a"])
    assert client.get(f"/maps/{job.pk}/files/pdf/").status_code == 404
    assert client.get(f"/maps/{job.pk}/preview/1/1/").status_code == 404


@pytest.mark.parametrize("section,sheet", [(0, 1), (2, 1), (1, 3), (1, 0), (99, 2)])
def test_preview_bad_numbers_404(client, users, login, done_job, section, sheet):
    job = done_job(users["a"])
    login(users["a"])
    assert client.get(f"/maps/{job.pk}/preview/{section}/{sheet}/").status_code == 404


def test_path_never_taken_from_request(client, users, login, done_job):
    job = done_job(users["a"])
    login(users["a"])
    for evil in ("../../db.sqlite3", "..%2F..%2Fdb.sqlite3", "%2e%2e/%2e%2e/x"):
        assert client.get(f"/maps/{job.pk}/files/{evil}/").status_code == 404
        assert client.get(f"/maps/{job.pk}/preview/{evil}/1/").status_code == 404
    # путь в базе, выходящий за каталог данных, не отдаётся
    MapJob.objects.filter(pk=job.pk).update(pdf_path="../outside.pdf")
    assert client.get(f"/maps/{job.pk}/files/pdf/").status_code == 404


def test_data_dir_not_served_as_static(client, users, login, done_job, settings):
    job = done_job(users["a"])
    login(users["a"])
    assert not settings.STATIC_ROOT.is_relative_to(settings.CORMAP_DATA_DIR)
    assert client.get(f"/static/{job.pdf_path}").status_code == 404
    assert client.get(f"/media/{job.pdf_path}").status_code == 404
