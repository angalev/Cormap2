"""Пределы времени, отмена и удаление карты в работе (ТЗ, раздел 9)."""

from __future__ import annotations

import pytest
from maps import storage, worker
from maps.models import AuditLog, MapJob

State = MapJob.State


class FakeProc:
    """Дочерний процесс для тестов: ``on_join`` вызывается на каждом ожидании.

    ``on_join(proc)`` изображает работу процесса и действия пользователя за это время;
    чтобы «завершить» процесс, выставляет ``proc.exitcode``.
    """

    def __init__(self, on_join=None) -> None:
        self.on_join = on_join
        self.exitcode: int | None = None
        self.killed = False
        self.joins = 0

    def join(self, timeout=None) -> None:
        self.joins += 1
        if self.on_join is not None and not self.killed:
            self.on_join(self)

    def kill(self) -> None:
        self.killed = True
        self.exitcode = -9


def _run(job: MapJob, proc: FakeProc) -> None:
    seen = []

    def start(pk: int) -> FakeProc:
        seen.append(pk)
        return proc

    worker.run_isolated(job, start=start, watch=0)
    assert seen == [job.pk]


def _hang(proc: FakeProc) -> None:
    """Процесс не завершается сам; страховка от бесконечного цикла в тесте."""
    assert proc.joins < 50, "обработчик не остановил зависший процесс"


# --- отмена ---------------------------------------------------------------------------------


@pytest.mark.parametrize("state", [State.UPLOADED, State.CHECKING])
def test_cancel_check(client, users, login, make_job, state):
    job = make_job(users["a"], state=state)
    login(users["a"])
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "Отменить проверку" in page and f"/maps/{job.pk}/delete/" in page
    assert "Предел времени — 30 мин" in page

    assert client.post(f"/maps/{job.pk}/cancel/").status_code == 302
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED and "отменена" in job.error_text
    assert AuditLog.objects.filter(action="check", job=job, details__contains="отменено").exists()
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "Проверка отменена пользователем." in page
    assert "Загрузить исправленную книгу" in page and "Отменить проверку" not in page
    assert client.get(f"/maps/{job.pk}/status/").json()["in_progress"] is False
    # отменённую проверку обработчик не берёт
    assert worker.process_all() == 0


@pytest.mark.parametrize("state", [State.QUEUED, State.BUILDING])
def test_cancel_build_returns_to_split(client, users, login, make_job, state):
    job = make_job(users["a"], check=True)
    MapJob.objects.filter(pk=job.pk).update(state=state, progress_done=2, progress_total=5)
    login(users["a"])
    assert "Отменить построение" in client.get(f"/maps/{job.pk}/").content.decode()

    assert client.post(f"/maps/{job.pk}/cancel/").status_code == 302
    job.refresh_from_db()
    assert job.state == State.CHECKED and job.progress_total == 0
    assert job.check_result and job.split  # проверка и разбиение сохранены
    assert AuditLog.objects.filter(action="build", job=job, details__contains="отменено").exists()
    assert "Построить карту" in client.get(f"/maps/{job.pk}/").content.decode()


def test_cancel_needs_work_in_progress(client, users, login, make_job):
    job = make_job(users["a"], check=True)
    login(users["a"])
    assert client.post(f"/maps/{job.pk}/cancel/").status_code == 302
    job.refresh_from_db()
    assert job.state == State.CHECKED
    assert client.get(f"/maps/{job.pk}/cancel/").status_code == 405


def test_foreign_map_cannot_be_cancelled(client, users, login, make_job):
    job = make_job(users["a"], state=State.CHECKING)
    for name in ("b", "admin"):  # администратор чужие карты не меняет
        login(users[name])
        assert client.post(f"/maps/{job.pk}/cancel/").status_code == 404
    job.refresh_from_db()
    assert job.state == State.CHECKING
    # администратор видит страницу чужой карты, но без кнопок
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "Идёт проверка" in page and "Отменить проверку" not in page


# --- удаление карты в работе --------------------------------------------------------------------


@pytest.mark.parametrize("state", [State.UPLOADED, State.CHECKING, State.QUEUED, State.BUILDING])
def test_delete_in_any_state(client, users, login, make_job, state):
    job = make_job(users["a"], state=state)
    source = storage.resolve(job.source_path)
    login(users["a"])
    assert client.get(f"/maps/{job.pk}/delete/").status_code == 200
    assert client.post(f"/maps/{job.pk}/delete/").status_code == 302
    assert not MapJob.objects.filter(pk=job.pk).exists()
    assert not source.exists()
    assert AuditLog.objects.filter(action="delete").exists()


# --- надзор за дочерним процессом ---------------------------------------------------------------


def test_isolated_job_runs_to_the_end(users, make_job):
    job = make_job(users["a"])
    claimed = worker.claim_next_job()

    def work(proc: FakeProc) -> None:
        worker.run_claimed_pk(job.pk)  # то, что делает дочерний процесс
        proc.exitcode = 0

    proc = FakeProc(work)
    _run(claimed, proc)
    job.refresh_from_db()
    assert job.state == State.CHECKED and not proc.killed


def test_runworker_loop_uses_child_process(users, make_job, monkeypatch):
    from maps.management.commands.runworker import Command

    job = make_job(users["a"])
    command = Command()

    def start(pk: int) -> FakeProc:
        def work(proc: FakeProc) -> None:
            worker.run_claimed_pk(pk)
            proc.exitcode = 0
            command.request_stop()

        return FakeProc(work)

    monkeypatch.setattr(worker.isolate, "start_child", start)
    command.run_loop(0)
    job.refresh_from_db()
    assert job.state == State.CHECKED


def test_cancel_stops_running_check(client, users, login, make_job, data_dir):
    job = make_job(users["a"])
    other = make_job(users["b"])
    login(users["a"])
    claimed = worker.claim_next_job()
    # недописанные файлы прерванной обработки; книга чужой карты должна остаться
    uploads = data_dir / "uploads"
    (uploads / "aaa_processed.xlsx").write_bytes(b"x")
    (uploads / "aaa_processed_trimmed.xlsx").write_bytes(b"x")
    (uploads / "bbb_processed.xlsx").write_bytes(b"x")
    MapJob.objects.filter(pk=other.pk).update(processed_path="uploads/bbb_processed.xlsx")

    def user_cancels(proc: FakeProc) -> None:
        _hang(proc)
        if proc.joins == 2:
            assert client.post(f"/maps/{job.pk}/cancel/").status_code == 302

    proc = FakeProc(user_cancels)
    _run(claimed, proc)
    assert proc.killed
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED and "отменена" in job.error_text
    assert sorted(p.name for p in uploads.glob("*_processed*")) == ["bbb_processed.xlsx"]
    assert storage.resolve(job.source_path).is_file()
    # очередь свободна: следующая карта проверяется
    MapJob.objects.filter(pk=other.pk).update(processed_path="")
    assert worker.process_all() == 1


def test_delete_stops_running_build(client, users, login, make_job):
    job = make_job(users["a"], check=True)
    MapJob.objects.filter(pk=job.pk).update(state=State.QUEUED)
    login(users["a"])
    claimed = worker.claim_next_job()
    results = storage.resolve(storage.job_results_rel(job.pk))

    def user_deletes(proc: FakeProc) -> None:
        _hang(proc)
        if proc.joins == 2:
            assert client.post(f"/maps/{job.pk}/delete/").status_code == 302
            # процесс успел дописать лист уже после удаления карты
            (results / "png").mkdir(parents=True)
            (results / "png" / "list_01_1.png").write_bytes(b"x")

    proc = FakeProc(user_deletes)
    _run(claimed, proc)
    assert proc.killed
    assert not MapJob.objects.filter(pk=job.pk).exists()
    assert not results.exists()


def test_check_time_limit(client, users, login, make_job, settings, monkeypatch, caplog):
    settings.CORMAP_CHECK_LIMIT_SECONDS = 120
    job = make_job(users["a"])
    other = make_job(users["b"])
    claimed = worker.claim_next_job()
    clock = iter(range(0, 10_000, 50))  # каждое обращение к часам - ещё 50 секунд
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))

    proc = FakeProc(_hang)
    with caplog.at_level("ERROR", logger="maps.worker"):
        _run(claimed, proc)
    assert proc.killed
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED
    assert "дольше 2 мин" in job.error_text and "остановлена" in job.error_text
    assert f"Задание № {job.pk} остановлено по пределу времени" in caplog.text
    assert AuditLog.objects.filter(action="check", job=job, details__contains="пределу").exists()
    # в очередь не возвращается; пользователь видит текст и может заменить книгу
    assert job.attempts == 1
    login(users["a"])
    page = client.get(f"/maps/{job.pk}/").content.decode()
    assert "дольше 2 мин" in page and "Загрузить исправленную книгу" in page
    assert worker.process_all() == 1
    other.refresh_from_db()
    assert other.state == State.CHECKED


def test_build_time_limit(users, make_job, settings, monkeypatch):
    settings.CORMAP_BUILD_LIMIT_SECONDS = 60
    job = make_job(users["a"], check=True)
    MapJob.objects.filter(pk=job.pk).update(state=State.QUEUED)
    claimed = worker.claim_next_job()
    results = storage.resolve(storage.job_results_rel(job.pk))
    results.mkdir(parents=True)
    (results / "karta.pdf").write_bytes(b"%PDF")
    clock = iter(range(0, 10_000, 50))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))

    proc = FakeProc(_hang)
    _run(claimed, proc)
    assert proc.killed
    job.refresh_from_db()
    assert job.state == State.ERROR and "дольше 1 мин" in job.error_text
    assert job.pdf_path == "" and not results.exists()
    assert job.check_result and job.split  # можно перестроить без новой проверки


def test_no_limit_when_zero(users, make_job, settings, monkeypatch):
    settings.CORMAP_CHECK_LIMIT_SECONDS = 0
    job = make_job(users["a"])
    claimed = worker.claim_next_job()
    clock = iter(range(0, 10**9, 10**6))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))

    def work(proc: FakeProc) -> None:
        if proc.joins == 5:
            worker.run_claimed_pk(job.pk)
            proc.exitcode = 0

    proc = FakeProc(work)
    _run(claimed, proc)
    job.refresh_from_db()
    assert job.state == State.CHECKED and not proc.killed
    assert job.limit_minutes == 0


def test_crashed_child_follows_attempts_rule(users, make_job, settings, caplog):
    """Дочерний процесс оборвался (не хватило памяти): возврат в очередь, затем снятие."""
    settings.CORMAP_MAX_ATTEMPTS = 2
    job = make_job(users["a"])

    def crash(proc: FakeProc) -> None:
        proc.exitcode = -9

    _run(worker.claim_next_job(), FakeProc(crash))
    job.refresh_from_db()
    assert job.state == State.UPLOADED and job.attempts == 1

    with caplog.at_level("ERROR", logger="maps.worker"):
        _run(worker.claim_next_job(), FakeProc(crash))
    job.refresh_from_db()
    assert job.state == State.CHECK_FAILED and job.error_text == worker.CRASH_TEXT
    assert "завершился аварийно" in caplog.text


def test_child_is_stopped_when_worker_is_interrupted(users, make_job):
    job = make_job(users["a"])
    claimed = worker.claim_next_job()

    def interrupt(proc: FakeProc) -> None:
        raise KeyboardInterrupt

    proc = FakeProc(interrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.run_isolated(claimed, start=lambda pk: proc, watch=0)
    assert proc.killed
    job.refresh_from_db()
    assert job.state == State.CHECKING  # вернётся в очередь при следующем запуске обработчика
