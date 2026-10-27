"""Хранение готовых карт: архив PNG при скачивании, удаление давних файлов, перестроение."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from pathlib import Path

from django.core.management import call_command
from django.utils import timezone
from maps import storage, worker
from maps.models import AuditLog, MapJob

State = MapJob.State
ROOT = Path(__file__).resolve().parents[2]


def _age(job, days: int, *, viewed: int | None = None) -> None:
    """Сдвигает в прошлое время построения (и, если задано, последнего открытия)."""
    now = timezone.now()
    MapJob.objects.filter(pk=job.pk).update(
        built_at=now - timedelta(days=days),
        viewed_at=None if viewed is None else now - timedelta(days=viewed),
    )


def _results(job) -> Path:
    return storage.resolve(storage.job_results_rel(job.pk))


def test_built_map_has_no_stored_archive_and_is_rebuilt_after_cleanup(
    client, users, login, make_job, settings
):
    settings.CORMAP_RESULTS_KEEP_DAYS = 90
    login(users["a"])
    job = make_job(users["a"], check=True)
    assert client.post(f"/maps/{job.pk}/build/").status_code == 302
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.DONE, job.error_text

    # архив на диске не лежит, но скачивается: собирается из листов
    assert job.zip_path == "" and not list(_results(job).glob("*.zip"))
    response = client.get(f"/maps/{job.pk}/files/png-zip/")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as zf:
        names = zf.namelist()
    assert names and all(n.startswith("list_") and n.endswith(".png") for n in names)
    assert len(names) == len(list(storage.resolve(job.png_dir).glob("*.png")))

    # карту давно не открывали: готовые файлы удаляются, сама карта остаётся
    _age(job, 120, viewed=100)
    assert worker.drop_stale_results() == 1
    job.refresh_from_db()
    assert job.state == State.DONE and job.pdf_path == "" and job.png_dir == ""
    assert not _results(job).exists()
    assert storage.resolve(job.source_path).is_file() and job.check_result and job.split

    # прямая ссылка на файл ведёт на страницу карты, а та запускает построение
    response = client.get(f"/maps/{job.pk}/files/pdf/")
    assert response.status_code == 302 and response["Location"] == f"/maps/{job.pk}/"
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "Идёт построение карты" in page
    job.refresh_from_db()
    assert job.state == State.QUEUED

    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.DONE, job.error_text
    assert client.get(f"/maps/{job.pk}/files/pdf/").status_code == 200


def test_recently_opened_or_built_maps_are_kept(users, done_job, settings):
    settings.CORMAP_RESULTS_KEEP_DAYS = 90
    opened = done_job(users["a"], check=True)
    _age(opened, 400, viewed=10)  # построена давно, но недавно открывали
    fresh = done_job(users["a"], check=True)
    _age(fresh, 10)  # построена недавно, не открывали
    assert worker.drop_stale_results() == 0
    for job in (opened, fresh):
        job.refresh_from_db()
        assert job.pdf_path and _results(job).is_dir()


def test_cleanup_can_be_switched_off(users, done_job, settings):
    settings.CORMAP_RESULTS_KEEP_DAYS = 0
    job = done_job(users["a"], check=True)
    _age(job, 1000)
    assert worker.drop_stale_results() == 0
    assert _results(job).is_dir()


def test_map_that_cannot_be_rebuilt_is_not_cleaned(users, done_job, settings):
    settings.CORMAP_RESULTS_KEEP_DAYS = 90
    job = done_job(users["a"])  # без результата проверки и разбиения: строить не из чего
    _age(job, 1000)
    assert worker.drop_stale_results() == 0
    assert _results(job).is_dir()


def test_opening_map_marks_it_viewed(client, users, login, done_job):
    login(users["a"])
    job = done_job(users["a"], check=True)
    assert job.viewed_at is None
    assert client.get(f"/maps/{job.pk}/").status_code == 200
    job.refresh_from_db()
    assert job.viewed_at is not None and job.state == State.DONE


def test_rebuild_missing_command_after_restore(users, done_job, capsys):
    """После восстановления из копии файлов результата нет: команда ставит карты в очередь."""
    lost = done_job(users["a"], check=True)
    kept = done_job(users["b"], check=True)
    storage.remove_tree(storage.job_results_rel(lost.pk))
    call_command("rebuild_missing")
    assert "Поставлено в очередь карт: 1" in capsys.readouterr().out
    lost.refresh_from_db()
    kept.refresh_from_db()
    assert lost.state == State.QUEUED and kept.state == State.DONE


def test_backup_takes_books_only():
    text = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    assert "for d in uploads; do" in text
    assert "uploads results" not in text


# --- срок хранения карты ----------------------------------------------------------------------


def test_expired_map_is_deleted_with_files(users, done_job, make_job, settings):
    settings.CORMAP_MAP_KEEP_DAYS = 365
    old = done_job(users["a"], check=True)
    fresh = done_job(users["a"], check=True)
    busy = make_job(users["b"])  # проверяется прямо сейчас: не трогаем даже просроченную
    long_ago = timezone.now() - timedelta(days=366)
    MapJob.objects.filter(pk__in=[old.pk, busy.pk]).update(created_at=long_ago)
    MapJob.objects.filter(pk=busy.pk).update(state=State.CHECKING)
    book, results = storage.resolve(old.source_path), _results(old)
    assert book.is_file() and results.is_dir()

    assert worker.drop_expired_maps() == 1
    assert not MapJob.objects.filter(pk=old.pk).exists()
    assert not book.exists() and not results.exists()
    assert MapJob.objects.filter(pk__in=[fresh.pk, busy.pk]).count() == 2
    entry = AuditLog.objects.filter(action="delete").get()
    assert entry.username == "система" and "истёк срок хранения" in entry.details


def test_expiry_can_be_switched_off(users, done_job, settings):
    settings.CORMAP_MAP_KEEP_DAYS = 0
    job = done_job(users["a"])
    MapJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=5000))
    assert worker.drop_expired_maps() == 0
    job.refresh_from_db()
    assert job.delete_after is None


def test_delete_date_is_shown_to_user(client, users, login, done_job, make_job, settings):
    settings.CORMAP_MAP_KEEP_DAYS = 365
    login(users["a"])
    done = done_job(users["a"], check=True)
    checked = make_job(users["a"], check=True)
    for job in (done, checked):
        expected = timezone.localtime(job.delete_after).strftime("%d.%m.%Y")
        assert (job.delete_after - job.created_at).days == 365
        page = client.get(f"/maps/{job.pk}/").content.decode()
        assert f"будет удалена автоматически {expected}" in page
    listing = client.get("/").content.decode()
    assert "Будет удалена" in listing and expected in listing
    assert "хранятся 365 сут." in listing
