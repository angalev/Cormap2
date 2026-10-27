"""Настройки для сервера (этап 9): адрес PostgreSQL, остановка обработчика, статика.

Драйвер psycopg и gunicorn не нужны: проверяются только разбор адреса и настройки.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from config.dburl import parse_database_url
from maps import worker
from maps.management.commands import runworker

ROOT = Path(__file__).resolve().parents[2]


def test_postgres_url_full():
    cfg = parse_database_url("postgres://cormap:p%40ss@127.0.0.1:5432/cormap")
    assert cfg["ENGINE"] == "django.db.backends.postgresql"
    assert cfg["NAME"] == "cormap"
    assert cfg["USER"] == "cormap"
    assert cfg["PASSWORD"] == "p@ss"
    assert cfg["HOST"] == "127.0.0.1"
    assert cfg["PORT"] == "5432"
    assert cfg["CONN_MAX_AGE"] == 60
    assert cfg["CONN_HEALTH_CHECKS"] is True


def test_postgres_url_unix_socket():
    cfg = parse_database_url("postgres:///cormap")
    assert cfg["NAME"] == "cormap"
    assert cfg["HOST"] == ""  # пустой host: unix-сокет, peer-аутентификация
    assert cfg["PORT"] == ""
    assert cfg["USER"] == ""
    assert cfg["CONN_MAX_AGE"] == 60


def test_sqlite_url_has_no_pooling_options():
    cfg = parse_database_url("sqlite:///x.sqlite3")
    assert "CONN_MAX_AGE" not in cfg


@pytest.fixture(autouse=True)
def _no_db_close(monkeypatch):
    """close_old_connections не должна трогать базу тестов."""
    monkeypatch.setattr(runworker, "close_old_connections", lambda: None)


def test_runworker_stops_after_current_job(monkeypatch):
    cmd = runworker.Command()
    calls = []

    def fake_process(isolated=False):
        calls.append("job")
        cmd.request_stop()  # как SIGTERM, пришедший во время задания
        return True

    monkeypatch.setattr(worker, "process_next_job", fake_process)
    monkeypatch.setattr(runworker, "close_old_connections", lambda: calls.append("close"))
    cmd.run_loop(0.01)
    assert calls == ["close", "job"]  # после задания новых не берёт


def test_runworker_stops_while_idle(monkeypatch):
    cmd = runworker.Command()
    polls = []

    def idle(isolated=False):
        polls.append(1)
        if len(polls) == 2:
            cmd.request_stop()
        return False

    monkeypatch.setattr(worker, "process_next_job", idle)
    cmd.run_loop(0.01)
    assert len(polls) == 2


def test_runworker_closes_old_connections_before_each_poll(monkeypatch):
    cmd = runworker.Command()
    events = []
    jobs = iter([True, True, False])

    def fake_process(isolated=False):
        events.append("poll")
        result = next(jobs)
        if not result:
            cmd.request_stop()
        return result

    monkeypatch.setattr(worker, "process_next_job", fake_process)
    monkeypatch.setattr(runworker, "close_old_connections", lambda: events.append("close"))
    cmd.run_loop(0.01)
    assert events == ["close", "poll"] * 3


def test_runworker_handle_exits_normally_and_restores_handler(monkeypatch, capsys):
    cmd = runworker.Command()
    monkeypatch.setattr(worker, "recover_stuck_jobs", lambda: 0)
    monkeypatch.setattr(worker, "run_cleanup", lambda: None)

    def fake_process(isolated=False):
        cmd.request_stop()
        return False

    monkeypatch.setattr(worker, "process_next_job", fake_process)
    before = signal.getsignal(signal.SIGTERM)
    cmd.handle(once=False, interval=0.01)  # возвращается без исключений: код выхода 0
    assert signal.getsignal(signal.SIGTERM) == before
    assert "остановлен" in capsys.readouterr().out


def test_runworker_installs_sigterm_handler(monkeypatch):
    cmd = runworker.Command()
    monkeypatch.setattr(worker, "recover_stuck_jobs", lambda: 0)
    monkeypatch.setattr(worker, "run_cleanup", lambda: None)
    seen = []

    def fake_process(isolated=False):
        seen.append(signal.getsignal(signal.SIGTERM))
        cmd.request_stop()
        return False

    monkeypatch.setattr(worker, "process_next_job", fake_process)
    cmd.handle(once=False, interval=0.01)
    assert seen == [cmd.request_stop]


def _run_python(args: list[str], env_extra: dict[str, str], tmp_path: Path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CORMAP_")}
    env.update(env_extra)
    env["PYTHONUTF8"] = "1"
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"  # настройки сервера, не тестовые
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )


def test_static_root_from_environment(tmp_path):
    static = tmp_path / "static"
    result = _run_python(
        ["-c", "from config import settings; print(settings.STATIC_ROOT)"],
        {
            "PYTHONPATH": str(ROOT / "web"),
            "CORMAP_DEBUG": "1",
            "CORMAP_DATA_DIR": str(tmp_path / "data"),
            "CORMAP_STATIC_ROOT": str(static),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == static


def test_static_root_default(tmp_path):
    result = _run_python(
        ["-c", "from config import settings; print(settings.STATIC_ROOT)"],
        {
            "PYTHONPATH": str(ROOT / "web"),
            "CORMAP_DEBUG": "1",
            "CORMAP_DATA_DIR": str(tmp_path / "data"),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == ROOT / "staticfiles"


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="нет SIGTERM")
def test_check_deploy_has_no_warnings(tmp_path):
    result = _run_python(
        ["manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        {
            "CORMAP_DEBUG": "0",
            "CORMAP_SECRET_KEY": "k7Gq2xV9mZp4Rt8LwY1cHs6Nd3Fb0JaUe5Xo" * 2,
            "CORMAP_ALLOWED_HOSTS": "example.org",
            "CORMAP_SSL_REDIRECT": "1",
            "CORMAP_DATA_DIR": str(tmp_path / "data"),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no issues" in result.stdout
