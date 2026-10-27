"""Фоновый обработчик: захват заданий, ошибки, восстановление после аварии."""

from __future__ import annotations

from django.core.management import call_command
from maps import storage, worker
from maps.models import AuditLog, MapJob

State = MapJob.State


def test_process_next_job_empty_queue(db):
    assert worker.process_next_job() is False


def test_claim_is_atomic(users, make_job):
    job = make_job(users["a"])
    first = worker.claim_next_job()
    assert first is not None and first.pk == job.pk
    assert first.state == State.CHECKING
    assert worker.claim_next_job() is None  # второй обработчик то же задание не возьмёт
    # условный захват по устаревшему состоянию ничего не меняет
    taken = MapJob.objects.filter(pk=job.pk, state=State.UPLOADED).update(state=State.CHECKING)
    assert taken == 0


def test_checks_go_before_builds(users, make_job):
    checked = make_job(users["a"], check=True)
    MapJob.objects.filter(pk=checked.pk).update(state=State.QUEUED)
    newer = make_job(users["b"])  # проверка поставлена позже построения, но берётся первой
    order = []
    for _ in range(2):
        taken = worker.claim_next_job()
        order.append((taken.pk, taken.state))
    assert order == [(newer.pk, State.CHECKING), (checked.pk, State.BUILDING)]


def test_oldest_first(users, make_job):
    first = make_job(users["a"])
    second = make_job(users["b"])
    assert worker.claim_next_job().pk == first.pk
    assert worker.claim_next_job().pk == second.pk


def test_check_failure_for_blocking_book(users, make_job, blocking_book):
    job = make_job(users["a"], book=blocking_book)
    assert worker.process_next_job() is True
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED
    assert job.error_text and "Traceback" not in job.error_text
    assert job.check_result["ok"] is False
    assert job.split is None
    assert AuditLog.objects.filter(
        action="check", job=job, details__contains="не пройдена"
    ).exists()


def test_broken_file_gives_check_failed(users, make_job, tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    job = make_job(users["a"], book=bad)
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED
    assert job.error_text


def test_missing_source_file_does_not_crash(users, make_job):
    job = make_job(users["a"])
    storage.resolve(job.source_path).unlink()
    assert worker.process_next_job() is True
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED
    assert "Traceback" not in job.error_text


def _queued_jobs(users, make_job, count):
    jobs = [make_job(users["a"]) for _ in range(count)]
    worker.process_all()  # проверка всех
    for job in jobs:
        MapJob.objects.filter(pk=job.pk).update(state=State.QUEUED)
        job.refresh_from_db()
    return jobs


def test_failed_build_gives_error_and_loop_continues(users, make_job, monkeypatch, caplog):
    jobs = _queued_jobs(users, make_job, 2)

    def boom(*args, **kwargs):
        raise RuntimeError("секретная внутренняя причина /tmp/secret")

    monkeypatch.setattr(worker, "build_map", boom)
    assert worker.process_all() == 2  # цикл не упал после первого задания
    for job in jobs:
        job.refresh_from_db()
        assert job.state == State.ERROR
        assert job.error_text
        assert "секретная" not in job.error_text and "Traceback" not in job.error_text
        assert not job.pdf_path
        assert not storage.resolve(storage.job_results_rel(job.pk)).exists()
    assert "секретная внутренняя причина" in caplog.text  # трассировка - в логе
    assert AuditLog.objects.filter(action="build", details__startswith="ошибка").count() == 2


def test_build_error_for_blocking_report_has_text(users, make_job, monkeypatch):
    from cormap.build import BuildError
    from cormap.model import CheckReport

    (job,) = _queued_jobs(users, make_job, 1)
    report = CheckReport()
    report.error("x", "Дистанция убывает", cell="A5", row=5)

    def refuse(*args, **kwargs):
        raise BuildError("текст", report)

    monkeypatch.setattr(worker, "build_map", refuse)
    worker.process_all()
    job.refresh_from_db()
    assert job.state == State.ERROR
    assert "A5: Дистанция убывает" in job.error_text
    assert job.status_text.startswith("ошибка: ")


def test_unexpected_exception_in_check_does_not_kill_worker(users, make_job, monkeypatch):
    job = make_job(users["a"])

    def boom(*args, **kwargs):
        raise ValueError("сбой")

    monkeypatch.setattr(worker, "run_check", boom)
    assert worker.process_next_job() is True
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED
    assert "Внутренняя ошибка" in job.error_text


def test_recover_stuck_jobs(users, make_job):
    stuck_check = make_job(users["a"], state=State.CHECKING)
    stuck_build = make_job(users["a"], state=State.BUILDING, progress_done=3, progress_total=10)
    fine = make_job(users["a"], state=State.DONE)
    assert worker.recover_stuck_jobs() == 2
    stuck_check.refresh_from_db()
    stuck_build.refresh_from_db()
    fine.refresh_from_db()
    assert stuck_check.state == State.UPLOADED
    assert stuck_build.state == State.QUEUED and stuck_build.progress_done == 0
    assert fine.state == State.DONE


def test_runworker_once_recovers_and_processes(users, make_job, capsys):
    stuck = make_job(users["a"], state=State.CHECKING)
    call_command("runworker", "--once")
    out = capsys.readouterr().out
    assert "Возвращено в очередь зависших заданий: 1" in out
    assert "Обработано заданий: 1" in out
    stuck.refresh_from_db()
    assert stuck.state == State.CHECKED


def test_progress_is_written(users, make_job, monkeypatch):
    (job,) = _queued_jobs(users, make_job, 1)
    seen = []

    def fake_build(source, params, out_dir, *, progress=None, **kwargs):
        for done in (1, 2, 3):
            progress(done, 3)
            seen.append(MapJob.objects.get(pk=job.pk).progress_done)
        raise RuntimeError("остановка после проверки прогресса")

    monkeypatch.setattr(worker, "build_map", fake_build)
    worker.process_all()
    assert seen == [1, 2, 3]
    job.refresh_from_db()
    assert job.status_text.startswith("ошибка")


def test_building_status_text(users, make_job):
    job = make_job(users["a"], state=State.BUILDING, progress_done=4, progress_total=10)
    assert job.status_text == "строится — лист 4 из 10"
    job.state = State.QUEUED
    assert job.status_text == "в очереди"
    job.state = State.DONE
    assert job.status_text == "готово"


def test_deleted_job_during_build_leaves_no_files(users, make_job, monkeypatch):
    (job,) = _queued_jobs(users, make_job, 1)
    rel = storage.job_results_rel(job.pk)

    def fake_build(source, params, out_dir, *, progress=None, **kwargs):
        from pathlib import Path

        Path(out_dir).mkdir(parents=True)
        (Path(out_dir) / "karta.pdf").write_bytes(b"%PDF")
        MapJob.objects.filter(pk=job.pk).delete()  # пользователь удалил карту во время построения

        return type(
            "R",
            (),
            {
                "sections": (),
                "pages": 0,
                "png_paths": [],
                "zip_path": None,
                "xlsx_path": None,
                "report": type("Rep", (), {"issues": []})(),
                "total_time": 0.0,
                "timings": {},
            },
        )()

    monkeypatch.setattr(worker, "build_map", fake_build)
    worker.process_all()
    assert not storage.resolve(rel).exists()


# --- подсказка «обработчик не запущен» -----------------------------------------------------


def _age(job, seconds: int) -> None:
    """Сдвигает время последнего изменения карты в прошлое."""
    from datetime import timedelta

    from django.utils import timezone

    MapJob.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(seconds=seconds))


def test_stall_hint_when_nobody_takes_the_job(users, make_job, login, settings):
    settings.CORMAP_STALL_SECONDS = 30
    client = login(users["a"])
    job = make_job(users["a"])

    # только что загружена: ждём молча
    assert client.get(f"/maps/{job.pk}/status/").json()["stalled"] is False
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert 'id="stall-hint" hidden' in page

    # ждёт дольше предела, обработчик ничем не занят: подсказка, опрос продолжается
    _age(job, 60)
    status = client.get(f"/maps/{job.pk}/status/").json()
    assert status["stalled"] is True and status["in_progress"] is True
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "обработчик заданий не запущен" in page
    assert 'id="stall-hint" hidden' not in page

    # обработчик заработал: карта проверена, подсказки нет
    assert worker.process_all() == 1
    assert client.get(f"/maps/{job.pk}/status/").json()["stalled"] is False


def test_no_stall_hint_while_worker_is_busy(users, make_job, login, settings):
    settings.CORMAP_STALL_SECONDS = 30
    client = login(users["a"])
    busy = make_job(users["b"])
    assert worker.claim_next_job().pk == busy.pk  # обработчик занят чужой картой
    job = make_job(users["a"])
    _age(job, 600)
    assert client.get(f"/maps/{job.pk}/status/").json()["stalled"] is False


def test_stall_hint_for_queued_build(users, make_job, login, settings):
    settings.CORMAP_STALL_SECONDS = 30
    client = login(users["a"])
    job = make_job(users["a"], check=True)
    assert client.post(f"/maps/{job.pk}/build/").status_code == 302
    _age(job, 60)
    assert client.get(f"/maps/{job.pk}/status/").json()["stalled"] is True
    assert "обработчик заданий не запущен" in client.get(f"/maps/{job.pk}/").content.decode()


# --- задание, на котором обработчик аварийно завершается ------------------------------------------


def test_crashing_check_is_failed_after_two_attempts(users, make_job, settings, caplog):
    """Обработчик дважды «упал» на проверке (например, не хватило памяти): задание снимается."""
    settings.CORMAP_MAX_ATTEMPTS = 2
    job = make_job(users["a"])
    other = make_job(users["b"])

    assert worker.claim_next_job().pk == job.pk  # 1-я попытка, обработчик «убит»
    assert worker.recover_stuck_jobs() == 1  # после перезапуска задание возвращено в очередь
    job.refresh_from_db()
    assert job.state == State.UPLOADED and job.attempts == 1

    assert worker.claim_next_job().pk == job.pk  # 2-я попытка, снова «убит»
    with caplog.at_level("ERROR", logger="maps.worker"):
        assert worker.recover_stuck_jobs() == 0  # в очередь больше не возвращается
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED and job.error_text == worker.CRASH_TEXT
    assert f"Задание № {job.pk} снято с ошибкой" in caplog.text and "памяти" in caplog.text

    # очередь не заблокирована: следующая карта проверяется
    assert worker.process_all() == 1
    other.refresh_from_db()
    assert other.state == State.CHECKED


def test_crashing_build_is_failed_and_user_sees_error(client, users, login, make_job, settings):
    settings.CORMAP_MAX_ATTEMPTS = 2
    login(users["a"])
    job = make_job(users["a"], check=True)
    assert client.post(f"/maps/{job.pk}/build/").status_code == 302
    for _ in range(2):
        assert worker.claim_next_job().pk == job.pk
        worker.recover_stuck_jobs()
    job.refresh_from_db()
    assert job.state == State.ERROR and job.pdf_path == ""
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "обработчик аварийно завершился" in page
    status = client.get(f"/maps/{job.pk}/status/").json()
    assert status["in_progress"] is False and "памяти" in status["error"]

    # пользователь запускает построение заново: счётчик попыток обнуляется
    assert client.post(f"/maps/{job.pk}/rebuild/").status_code == 302
    assert client.post(f"/maps/{job.pk}/build/").status_code == 302
    job.refresh_from_db()
    assert job.state == State.QUEUED and job.attempts == 0
