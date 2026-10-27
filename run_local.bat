@echo off
chcp 65001 >nul
rem Локальный запуск: сайт и обработчик заданий вместе (ТЗ, раздел 9).
rem Обработчик открывается в отдельном окне; без него проверка и построение не идут.
rem Остановка: Ctrl+C в этом окне (сайт) и закрыть окно обработчика.
cd /d "%~dp0"
rem Локальный режим включается явно: без этой переменной сайт требует настроек сервера.
set "CORMAP_DEBUG=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Не найдено окружение .venv. Создайте его и установите зависимости.
    exit /b 1
)
"%PY%" manage.py migrate --noinput
if errorlevel 1 exit /b 1
rem Демо-доступ: admin / ivanov / petrov, пароль Karta-2026-test (см. README).
"%PY%" manage.py seed_demo
start "cormap worker" "%PY%" manage.py runworker
"%PY%" manage.py runserver
