"""Сборка архива поставки (tools/make_bundle.py) и файлы deploy/ (этап 9)."""

from __future__ import annotations

import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools import make_bundle

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
FORBIDDEN_PARTS = {
    "__pycache__",
    "var",
    ".venv",
    "tests",
    "reference",
    "starter",
    "out",
    "tools",
    "wheels",
    "staticfiles",
}


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Iterator[tarfile.TarFile]:
    path = make_bundle.build_bundle(tmp_path_factory.mktemp("bundle"))
    assert path.name == f"cormap-{make_bundle.project_version()}.tar.gz"
    with tarfile.open(path) as tar:
        yield tar


def test_bundle_layout(bundle):
    prefix = f"cormap-{make_bundle.project_version()}"
    names = bundle.getnames()
    assert all(n == prefix or n.startswith(prefix + "/") for n in names)
    rel = {n[len(prefix) + 1 :] for n in names if n != prefix}
    for expected in (
        "manage.py",
        "pyproject.toml",
        "cormap/__init__.py",
        "web/config/settings.py",
        "web/maps/worker.py",
        "deploy/install.sh",
        "deploy/cormap-manage",
        "deploy/nginx.conf",
        "deploy/cormap-web.service",
        "deploy/cormap-web.socket",
        "deploy/constraints.txt",
        "deploy/cormap-worker.service",
        "deploy/backup.sh",
        "deploy/certbot.sh",
        "deploy/cormap-backup.service",
        "deploy/cormap-backup.timer",
    ):
        assert expected in rel, expected
    assert any(r.startswith("cormap/assets/icons/png/") for r in rel)  # данные пакета


def test_bundle_has_nothing_extra(bundle):
    prefix = f"cormap-{make_bundle.project_version()}"
    for name in bundle.getnames():
        parts = name.removeprefix(prefix).strip("/").split("/")
        assert not FORBIDDEN_PARTS.intersection(parts), name
        assert not name.endswith(".pyc"), name
        assert not name.startswith(f"{prefix}/web/tests"), name
    top = {n.split("/")[1] for n in bundle.getnames() if "/" in n}
    assert top == {"cormap", "web", "deploy", "manage.py", "pyproject.toml"}


def test_bundle_line_endings_and_modes(bundle):
    prefix = f"cormap-{make_bundle.project_version()}"
    executable = {"manage.py", "deploy/cormap-manage"}
    for member in bundle.getmembers():
        if not member.isfile():
            continue
        rel = member.name[len(prefix) + 1 :]
        data = bundle.extractfile(member).read()
        if rel.startswith("deploy/") or rel == "manage.py":
            assert b"\r" not in data, rel
        if rel.endswith(".sh") or rel in executable:
            assert member.mode == 0o755, rel
        else:
            assert member.mode == 0o644, rel


def test_requirements_include_server_group_and_build_tools():
    reqs = " ".join(make_bundle.requirements())
    for name in ("openpyxl", "Django", "psycopg", "gunicorn", "setuptools", "wheel", "pip"):
        assert name in reqs, name


def _pins() -> dict[str, str]:
    """Имя пакета (в нижнем регистре, с дефисами) -> версия из deploy/constraints.txt."""
    pins = {}
    for line in (DEPLOY / "constraints.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, sep, version = line.partition("==")
            assert sep and version.strip(), f"не точная версия: {line}"
            pins[name.strip().lower().replace("_", "-")] = version.strip()
    return pins


def test_every_server_requirement_is_pinned():
    from packaging.requirements import Requirement

    pins = _pins()
    for text in make_bundle.requirements():
        req = Requirement(text)
        name = req.name.lower().replace("_", "-")
        assert name in pins, f"{req.name} нет в deploy/constraints.txt"
        assert req.specifier.contains(pins[name]), f"{text}: закреплена {pins[name]}"
    assert "psycopg-binary" in pins  # зависимость дополнения psycopg[binary]


def test_installed_packages_match_pins():
    """Тесты идут на тех же версиях, что закреплены для сервера."""
    from importlib.metadata import PackageNotFoundError, version

    checked = 0
    for name, pinned in _pins().items():
        if name in ("pip", "setuptools", "wheel"):  # средства сборки, не код сервиса
            continue
        try:
            installed = version(name)
        except PackageNotFoundError:  # gunicorn и psycopg нужны только на сервере
            continue
        assert installed == pinned, f"{name}: установлена {installed}, закреплена {pinned}"
        checked += 1
    assert checked >= 8


def test_wheels_and_install_use_pins():
    assert (ROOT / make_bundle.CONSTRAINTS).is_file()
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert 'PIP_CONSTRAINT="$APP_DIR/deploy/constraints.txt"' in install


def _deploy_files() -> list[Path]:
    return sorted(p for p in DEPLOY.iterdir() if p.is_file())


def test_deploy_files_are_lf_utf8_without_bom():
    files = _deploy_files()
    assert files
    for path in files:
        data = path.read_bytes()
        assert b"\r" not in data, f"{path.name}: есть CR"
        assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name}: BOM"
        data.decode("utf-8")


def test_deploy_scripts_have_bash_shebang():
    scripts = [p for p in _deploy_files() if p.suffix == ".sh" or p.name == "cormap-manage"]
    assert {p.name for p in scripts} >= {"install.sh", "backup.sh", "certbot.sh", "cormap-manage"}
    for path in scripts:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first in ("#!/usr/bin/env bash", "#!/bin/bash"), path.name
