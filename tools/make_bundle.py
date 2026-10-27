"""Сборка архива поставки: out/cormap-<версия>.tar.gz (этап 9).

Работает на Windows без внешних программ (только стандартная библиотека; для ключа
--wheels нужен pip текущего окружения и доступ к PyPI).

В архив входят cormap/, web/ (без web/tests), manage.py, pyproject.toml, deploy/.
Окончания строк в deploy/ и manage.py приводятся к LF, у сценариев права 0755.

Запуск: python tools/make_bundle.py [--wheels] [--out папка]
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import io
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# что включается в архив (относительно корня проекта)
INCLUDE_DIRS = ("cormap", "web", "deploy")
INCLUDE_FILES = ("manage.py", "pyproject.toml")

# имена каталогов и файлов, которые в архив не попадают, где бы они ни встретились
EXCLUDE_NAMES = (
    "__pycache__",
    "*.pyc",
    "*.egg-info",
    "var",
    ".venv",
    "tests",
    "reference",
    "starter",
    "out",
    "tools",
    "staticfiles",
    "wheels",
)

# файлы с окончаниями строк LF и правами 0755
LF_PREFIXES = ("deploy/",)
LF_FILES = ("manage.py",)
EXECUTABLE_NAMES = ("manage.py", "cormap-manage")
EXECUTABLE_SUFFIXES = (".sh",)

# платформа сервера для скачивания колёс
WHEEL_PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_28_x86_64")
WHEEL_PYTHON = "3.12"
# нужны для установки самого пакета cormap без сети (сборка в изолированном окружении)
BUILD_REQUIREMENTS = ("pip", "setuptools>=68", "wheel")
# точные версии всех пакетов сервера
CONSTRAINTS = Path("deploy") / "constraints.txt"


def project_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def requirements(root: Path = ROOT) -> list[str]:
    """Зависимости ядра и групп web, server плюс pip/setuptools/wheel."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = project.get("optional-dependencies", {})
    result = list(project.get("dependencies", []))
    for group in ("web", "server"):
        result.extend(extras.get(group, []))
    result.extend(BUILD_REQUIREMENTS)
    return result


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_NAMES)


def _is_excluded_tree(rel: Path) -> bool:
    if rel.parts[:2] == ("web", "tests"):
        return True
    return any(_excluded(part) for part in rel.parts)


def collect_files(root: Path = ROOT) -> list[Path]:
    """Относительные пути файлов поставки, отсортированные."""
    files: list[Path] = []
    for name in INCLUDE_FILES:
        files.append(Path(name))
    for directory in INCLUDE_DIRS:
        for path in sorted((root / directory).rglob("*")):
            rel = path.relative_to(root)
            if path.is_file() and not _is_excluded_tree(rel):
                files.append(rel)
    return files


def _file_mode(rel: Path) -> int:
    if rel.name in EXECUTABLE_NAMES or rel.suffix in EXECUTABLE_SUFFIXES:
        return 0o755
    return 0o644


def _file_bytes(root: Path, rel: Path) -> bytes:
    data = (root / rel).read_bytes()
    posix = rel.as_posix()
    if posix in LF_FILES or posix.startswith(LF_PREFIXES):
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int, mtime: float) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(mtime)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def _add_dir(tar: tarfile.TarFile, name: str, mtime: float) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.mtime = int(mtime)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tar.addfile(info)


def download_wheels(dest: Path, root: Path = ROOT) -> None:
    """Скачивает колёса для Ubuntu x86_64 / Python 3.12; при неудаче - SystemExit с пояснением."""
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(dest),
        "--python-version",
        WHEEL_PYTHON,
        "--implementation",
        "cp",
        "--only-binary=:all:",
        "--disable-pip-version-check",
        "--constraint",  # точные версии: в архив попадает то же, что поставит install.sh
        str(root / CONSTRAINTS),
    ]
    for platform in WHEEL_PLATFORMS:
        command += ["--platform", platform]
    command += requirements(root)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-15:]
        raise SystemExit(
            "Не удалось скачать колёса зависимостей (нужен доступ к PyPI):\n" + "\n".join(tail)
        )
    if not any(p.name.lower().startswith("setuptools-") for p in dest.iterdir()):
        raise SystemExit("В скачанных колёсах нет setuptools: пакет cormap не соберётся без сети.")


def build_bundle(out_dir: Path, *, wheels: bool = False, root: Path = ROOT) -> Path:
    """Собирает архив и возвращает путь к нему."""
    version = project_version(root)
    prefix = f"cormap-{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{prefix}.tar.gz"
    files = collect_files(root)
    mtime = max((root / rel).stat().st_mtime for rel in files)

    with tempfile.TemporaryDirectory() as tmp:
        wheel_files: list[Path] = []
        if wheels:
            wheel_dir = Path(tmp) / "wheels"
            wheel_dir.mkdir()
            download_wheels(wheel_dir, root)
            wheel_files = sorted(wheel_dir.iterdir())

        with target.open("wb") as raw:
            # mtime=0 в заголовке gzip: архив из одних и тех же файлов получается одинаковым
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
                    _add_dir(tar, prefix, mtime)
                    dirs: set[str] = set()
                    for rel in files:
                        for parent in reversed(rel.parents):
                            if parent.parts:
                                dirs.add(parent.as_posix())
                    if wheel_files:
                        dirs.add("wheels")
                    for directory in sorted(dirs):
                        _add_dir(tar, f"{prefix}/{directory}", mtime)
                    for rel in files:
                        _add_bytes(
                            tar,
                            f"{prefix}/{rel.as_posix()}",
                            _file_bytes(root, rel),
                            _file_mode(rel),
                            (root / rel).stat().st_mtime,
                        )
                    for wheel in wheel_files:
                        _add_bytes(
                            tar, f"{prefix}/wheels/{wheel.name}", wheel.read_bytes(), 0o644, mtime
                        )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка архива поставки.")
    parser.add_argument("--wheels", action="store_true", help="добавить колёса зависимостей")
    parser.add_argument("--out", type=Path, default=ROOT / "out", help="папка для архива")
    args = parser.parse_args()
    path = build_bundle(args.out, wheels=args.wheels)
    print(f"Архив готов: {path} ({path.stat().st_size / 1024 / 1024:.1f} МБ)")


if __name__ == "__main__":
    main()
