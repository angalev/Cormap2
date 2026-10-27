"""Фоновая обработка: проверка книг и построение карт.

Обработчик - отдельный процесс (``python manage.py runworker``). Он берёт по одному
заданию: сначала проверки (``uploaded``), затем построения (``queued``). Захват задания
атомарный: условный UPDATE ``WHERE state = <старое>`` меняет состояние ровно у одного
из конкурирующих обработчиков (у остальных он вернёт 0 изменённых строк).

Служба выполняет задание в дочернем процессе (``run_isolated``) и следит за ним: при
отмене, удалении карты или истечении предела времени процесс останавливается, а
обработчик берёт следующее задание (ТЗ, раздел 9).

Исключение в задании не роняет обработчик: пользователю - короткий текст без
трассировки, полная трассировка - в лог.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from cormap.build import PDF_NAME, PNG_DIR, XLSX_NAME, BuildError, build_map
from cormap.check import check_and_split
from cormap.defects import defect_objects
from cormap.model import ERROR, WARNING
from cormap.params import ParamsError
from cormap.preprocess import RefineOptions, run_refinery_step
from cormap.serialize import notes_to_dict, objects_to_list, report_to_dict
from maps import audit, isolate, storage
from maps.models import AuditLog, MapJob
from maps.services import (
    book_path,
    book_sheets,
    error_summary,
    is_split_issue,
    make_split_state,
    params_from_job,
    refinery_enabled,
    refinery_options,
)

logger = logging.getLogger("maps.worker")

State = MapJob.State

# Текст для пользователя, когда задание снято после аварий обработчика
CRASH_TEXT = (
    "Не удалось обработать карту: обработчик аварийно завершился (возможно, не хватило "
    "памяти). Попробуйте уменьшить книгу или обратитесь к администратору."
)

# Сколько предупреждений построения сохраняется в сведениях о карте
MAX_BUILD_WARNINGS = 50

# Как часто обработчик сверяет дочерний процесс с базой и пределом времени, секунд
WATCH_SECONDS = 1.0
# Сколько ждать завершения остановленного дочернего процесса, секунд
KILL_WAIT_SECONDS = 10.0


def recover_stuck_jobs(pk: int | None = None) -> int:
    """Возвращает в очередь задания, зависшие после аварийной остановки обработчика.

    С ``pk`` - только это задание (у него аварийно завершился дочерний процесс).

    Задание, на котором обработчик обрывался уже CORMAP_MAX_ATTEMPTS раз, в очередь не
    возвращается: оно снимается с ошибкой (иначе обработчик падал бы на нём бесконечно,
    например при нехватке памяти), а подробности пишутся в лог.
    """
    now = timezone.now()
    limit = max(int(getattr(settings, "CORMAP_MAX_ATTEMPTS", 2)), 1)
    jobs = MapJob.objects.all() if pk is None else MapJob.objects.filter(pk=pk)
    for state, after in ((State.CHECKING, State.CHECK_FAILED), (State.BUILDING, State.ERROR)):
        poisoned = jobs.filter(state=state, attempts__gte=limit)
        for pk, attempts in list(poisoned.values_list("pk", "attempts")):
            failed = MapJob.objects.filter(pk=pk, state=state).update(
                state=after, error_text=CRASH_TEXT, updated_at=now
            )
            if not failed:
                continue
            if state == State.BUILDING:
                MapJob.objects.filter(pk=pk).update(
                    pdf_path="", zip_path="", xlsx_path="", png_dir="", result_info=None
                )
                storage.remove_tree(storage.job_results_rel(pk))
            logger.error(
                "Задание № %s снято с ошибкой: обработчик аварийно завершался на нём %d раз "
                "подряд (состояние «%s»). Вероятная причина - нехватка памяти; "
                "см. журнал системы (journalctl -k, строки oom-kill).",
                pk,
                attempts,
                state,
            )
    checks = jobs.filter(state=State.CHECKING).update(state=State.UPLOADED, updated_at=now)
    builds = jobs.filter(state=State.BUILDING).update(
        state=State.QUEUED, progress_done=0, progress_total=0, updated_at=now
    )
    if checks or builds:
        logger.warning("Возвращено в очередь заданий: проверок %d, построений %d", checks, builds)
    return checks + builds


def drop_stale_results() -> int:
    """Удаляет готовые файлы карт, которые давно не открывали (ТЗ, раздел 9).

    Книга, параметры, результат проверки и разбиение остаются: при открытии карта
    строится заново. Возвращает число карт, у которых удалены файлы.
    """
    days = int(getattr(settings, "CORMAP_RESULTS_KEEP_DAYS", 0))
    if days <= 0:
        return 0
    limit = timezone.now() - timedelta(days=days)
    stale = (
        MapJob.objects.filter(state=State.DONE, built_at__lt=limit)
        .filter(Q(viewed_at__lt=limit) | Q(viewed_at__isnull=True))
        .exclude(pdf_path="")
        .exclude(check_result__isnull=True)
        .exclude(split__isnull=True)
        .values_list("pk", flat=True)
    )
    count = 0
    for pk in list(stale):
        dropped = MapJob.objects.filter(pk=pk, state=State.DONE).update(
            pdf_path="", zip_path="", xlsx_path="", png_dir=""
        )
        if dropped:
            storage.remove_tree(storage.job_results_rel(pk))
            count += 1
    if count:
        logger.info("Удалены готовые файлы давно не открывавшихся карт: %d", count)
    return count


def drop_expired_maps() -> int:
    """Удаляет карты целиком по истечении срока хранения (ТЗ, раздел 9).

    Удаляются запись, книга и готовые файлы; в журнале остаётся запись об удалении.
    """
    days = int(getattr(settings, "CORMAP_MAP_KEEP_DAYS", 0))
    if days <= 0:
        return 0
    limit = timezone.now() - timedelta(days=days)
    expired = MapJob.objects.filter(created_at__lt=limit).exclude(state__in=MapJob.WORKING_STATES)
    count = 0
    for job in list(expired):
        audit.log(
            AuditLog.Action.DELETE,
            username="система",
            job=job,
            object_repr=str(job),
            details=f"автоматически: истёк срок хранения {days} сут., файл «{job.original_name}»",
        )
        job.delete()  # файлы удаляет обработчик post_delete
        count += 1
    if count:
        logger.info("Удалены карты с истёкшим сроком хранения: %d", count)
    return count


def run_cleanup() -> None:
    """Периодическая уборка: карты с истёкшим сроком и давние готовые файлы."""
    drop_expired_maps()
    drop_stale_results()


def claim_next_job() -> MapJob | None:
    """Атомарно берёт следующее задание: проверки раньше построений, старые раньше новых."""
    for before, after in ((State.UPLOADED, State.CHECKING), (State.QUEUED, State.BUILDING)):
        candidates = (
            MapJob.objects.filter(state=before)
            .order_by("created_at", "pk")
            .values_list("pk", flat=True)[:20]
        )
        for pk in candidates:
            taken = MapJob.objects.filter(pk=pk, state=before).update(
                state=after,
                attempts=F("attempts") + 1,
                error_text="",
                updated_at=timezone.now(),
            )
            if taken == 1:
                return MapJob.objects.get(pk=pk)
    return None


def process_next_job(isolated: bool = False) -> bool:
    """Обрабатывает одно задание. True - задание было, False - очередь пуста.

    ``isolated`` - в дочернем процессе, с пределом времени и остановкой при отмене
    (так работает служба); иначе в своём процессе (``runworker --once`` и тесты).
    """
    job = claim_next_job()
    if job is None:
        return False
    if isolated:
        run_isolated(job)
    else:
        run_claimed(job)
    return True


def run_claimed(job: MapJob) -> None:
    """Выполняет захваченное задание в этом процессе."""
    try:
        if job.state == State.CHECKING:
            run_check(job)
        else:
            run_build(job)
    except Exception:  # обработчик не должен падать из-за одного задания
        logger.exception("Необработанная ошибка в задании № %s", job.pk)
        _fail_unexpected(job)


def run_claimed_pk(pk: int) -> None:
    """Вход дочернего процесса: задание уже захвачено родителем (maps/isolate.py)."""
    job = MapJob.objects.filter(pk=pk, state__in=MapJob.WORKING_STATES).first()
    if job is not None:  # иначе карту успели отменить или удалить
        run_claimed(job)


# --- задание в дочернем процессе: предел времени, отмена, удаление -------------------------------


def time_limit(state: str) -> int:
    """Предел времени задания в этом состоянии, секунд (0 - без предела)."""
    name = "CORMAP_CHECK_LIMIT_SECONDS" if state == State.CHECKING else "CORMAP_BUILD_LIMIT_SECONDS"
    return max(int(getattr(settings, name, 0)), 0)


def limit_text(state: str, limit: int) -> str:
    """Текст для пользователя: задание остановлено по пределу времени."""
    minutes = f"{limit / 60:g} мин"
    if state == State.CHECKING:
        return (
            f"Проверка книги шла дольше {minutes} и остановлена. Уменьшите книгу или "
            "увеличьте шаг интерполяции и загрузите её снова, либо обратитесь к администратору."
        )
    return (
        f"Построение карты шло дольше {minutes} и остановлено. Попробуйте ещё раз "
        "или обратитесь к администратору."
    )


def run_isolated(job: MapJob, *, start=None, watch: float = WATCH_SECONDS) -> None:
    """Выполняет захваченное задание в дочернем процессе и следит за ним.

    Процесс останавливается, если карту отменили или удалили (состояние в базе уже не
    то, в котором задание взято) или истёк предел времени. Аварийно завершившийся
    процесс - как авария обработчика: задание возвращается в очередь или снимается.
    ``start`` - запуск процесса (подменяется в тестах).
    """
    state = job.state
    limit = time_limit(state)
    proc = (start or isolate.start_child)(job.pk)
    started = time.monotonic()
    try:
        while True:
            proc.join(watch)
            if proc.exitcode is not None:
                break
            if not MapJob.objects.filter(pk=job.pk, state=state).exists():
                _stop_child(proc)
                logger.info("Задание № %s остановлено: карта отменена или удалена", job.pk)
                _drop_leftovers(job.pk, state)
                return
            if limit and time.monotonic() - started > limit:
                _stop_child(proc)
                _fail_by_limit(job, state, limit)
                _drop_leftovers(job.pk, state)
                return
    finally:
        if proc.exitcode is None:  # обработчик останавливают, не дожидаясь задания
            _stop_child(proc)
    if not MapJob.objects.filter(pk=job.pk, state=state).exists():
        return
    # процесс вышел, а задание осталось «в работе»: он оборвался (например, не хватило памяти)
    logger.error(
        "Дочерний процесс задания № %s завершился аварийно (код %s)", job.pk, proc.exitcode
    )
    _drop_leftovers(job.pk, state)
    recover_stuck_jobs(job.pk)


def _stop_child(proc) -> None:
    proc.kill()
    proc.join(KILL_WAIT_SECONDS)


def _fail_by_limit(job: MapJob, state: str, limit: int) -> None:
    text = limit_text(state, limit)
    after = State.CHECK_FAILED if state == State.CHECKING else State.ERROR
    failed = MapJob.objects.filter(pk=job.pk, state=state).update(
        state=after, error_text=text, updated_at=timezone.now()
    )
    if not failed:  # карту отменили или удалили в тот же момент
        return
    logger.error("Задание № %s остановлено по пределу времени %d с (%s)", job.pk, limit, state)
    action = AuditLog.Action.CHECK if state == State.CHECKING else AuditLog.Action.BUILD
    audit.log(action, user=job.owner, job=job, details=f"остановлено по пределу времени: {text}")
    if state == State.BUILDING:
        _clear_result_paths(job)


def _drop_leftovers(pk: int, state: str) -> None:
    """Убирает недописанные файлы прерванного задания."""
    if state == State.CHECKING:
        storage.remove_orphan_processed(
            set(MapJob.objects.exclude(processed_path="").values_list("processed_path", flat=True))
        )
    elif not MapJob.objects.filter(pk=pk, state=State.DONE).exists():
        storage.remove_tree(storage.job_results_rel(pk))


def process_all() -> int:
    """Обрабатывает всё, что есть в очереди (для ``runworker --once`` и тестов)."""
    count = 0
    while process_next_job():
        count += 1
    return count


def _fail_unexpected(job: MapJob) -> None:
    text = "Внутренняя ошибка обработчика. Попробуйте ещё раз или обратитесь к администратору."
    if job.state == State.CHECKING:
        MapJob.objects.filter(pk=job.pk, state=State.CHECKING).update(
            state=State.CHECK_FAILED, error_text=text, updated_at=timezone.now()
        )
    else:
        MapJob.objects.filter(pk=job.pk, state=State.BUILDING).update(
            state=State.ERROR, error_text=text, updated_at=timezone.now()
        )


# --- проверка ----------------------------------------------------------------------------


def run_check(job: MapJob) -> None:
    """Проверка книги и предложенное разбиение (автоматическое)."""
    try:
        params = params_from_job(job)
    except (ParamsError, TypeError) as exc:
        _finish_check_failed(job, f"Неверные параметры карты: {exc}", None)
        return
    refinery_info = None
    options = refinery_options(job) if refinery_enabled() else None
    # без флажков модуль не вызывается: проверяется лист Template самой книги (ТЗ, 4.5)
    if options is not None and options.any_flag:
        refinery_info = _run_refinery(job, options)
        if refinery_info is None:  # карту удалили или состояние изменилось
            return
        if not refinery_info["ok"]:
            text = (
                f"Ошибка обработки данных [{refinery_info['error_code']}]: "
                f"{refinery_info['error_message']}"
            )
            _finish_check_failed(
                job, text, {"refinery": refinery_info}, extra=_refinery_log_text(refinery_info)
            )
            return
    extra_data = {"refinery": refinery_info} if refinery_info else None
    try:
        path = book_path(job)
        survey, report, notes, split = check_and_split(
            path, params.split_text, params.paper, params.start_km, sheets=book_sheets(job)
        )
    except Exception:
        logger.exception("Сбой проверки книги, задание № %s", job.pk)
        _finish_check_failed(
            job,
            "Не удалось прочитать книгу. Проверьте файл и загрузите снова.",
            extra_data,
            extra=_refinery_log_text(refinery_info),
        )
        return

    data = report_to_dict(report)
    if refinery_info:
        data["refinery"] = refinery_info
    # замечания разбиения хранятся вместе с разбиением (они меняются при пересчёте)
    data["issues"] = [i for i in data["issues"] if not is_split_issue(i["code"])]
    split_error = next((i for i in report.errors if i.code == "split_error"), None)
    if notes is not None:
        data["notes"] = notes_to_dict(notes)
    if survey is not None and notes is not None:
        rng = survey.distance_range
        data["dist_range"] = list(rng) if rng is not None else None
        data["objects"] = objects_to_list((*notes.objects, *defect_objects(survey)))

    if split is None or not report.ok:
        errors = [i for i in data["issues"] if i["level"] == ERROR]
        if split_error is not None:
            errors.append({"message": split_error.message, "cell": None, "row": None})
        _finish_check_failed(
            job, error_summary(errors), data, extra=_refinery_log_text(refinery_info)
        )
        return

    MapJob.objects.filter(pk=job.pk, state=State.CHECKING).update(
        state=State.CHECKED,
        check_result=data,
        split=make_split_state(split, params.split),
        error_text="",
        updated_at=timezone.now(),
    )
    n_warn = sum(1 for i in data["issues"] if i["level"] == WARNING)
    details = f"проверка пройдена, предупреждений: {n_warn}, листов: {len(split.sections)}"
    audit.log(
        AuditLog.Action.CHECK,
        user=job.owner,
        job=job,
        details=_with_refinery(details, _refinery_log_text(refinery_info)),
    )


def _finish_check_failed(job: MapJob, text: str, data: dict | None, extra: str = "") -> None:
    MapJob.objects.filter(pk=job.pk, state=State.CHECKING).update(
        state=State.CHECK_FAILED,
        check_result=data,
        split=None,
        error_text=text,
        updated_at=timezone.now(),
    )
    details = _with_refinery(f"не пройдена: {text}", extra)
    audit.log(AuditLog.Action.CHECK, user=job.owner, job=job, details=details[:500])


def _with_refinery(details: str, extra: str) -> str:
    return f"{details}; {extra}" if extra else details


# --- обработка данных модулем refinery (ТЗ, 4.5) ------------------------------------------------


def _run_refinery(job: MapJob, options: RefineOptions) -> dict | None:
    """Шаг обработки: исходная книга -> обработанная (новый файл в каталоге загрузок).

    Возвращает сведения для отчёта и журнала (``ok``, флажки, шаг, сид, статистика или
    код и текст ошибки). Если обработанная книга уже есть (повторный запуск проверки без
    замены книги), модуль не вызывается. None - карту удалили или её состояние изменилось.
    """
    if job.processed_path and storage.resolve(job.processed_path).is_file():
        previous = (job.check_result or {}).get("refinery")
        if isinstance(previous, dict) and previous.get("ok"):
            return previous
        return _refinery_info(options, None)
    rel = storage.new_processed_name()
    outcome = run_refinery_step(storage.resolve(job.source_path), storage.resolve(rel), options)
    info = _refinery_info(options, outcome)
    if not outcome.ok:
        logger.warning(
            "Ошибка обработки данных [%s], задание № %s: %s",
            outcome.error_code,
            job.pk,
            outcome.error_message,
        )
        return info
    saved = MapJob.objects.filter(pk=job.pk, state=State.CHECKING).update(processed_path=rel)
    if not saved:
        storage.remove_file(rel)
        return None
    job.processed_path = rel
    return info


def _refinery_info(options: RefineOptions, outcome) -> dict:
    flags = options.flags()
    info = {
        "ok": outcome is None or outcome.ok,
        "flags": [name for name, on in flags.items() if on],
        "labels": options.enabled_labels(),
        "step": options.step,
        "seed": options.random_seed,
        "stats": outcome.stats.to_dict() if outcome is not None and outcome.stats else None,
    }
    if outcome is not None and not outcome.ok:
        info["error_code"] = outcome.error_code
        info["error_message"] = outcome.error_message
    return info


def _refinery_log_text(info: dict | None) -> str:
    """Сведения о вызове модуля для журнала: флажки, шаг, сид, статистика (или ошибка)."""
    if not info:
        return ""
    text = (
        f"обработка данных: флажки {', '.join(info['flags']) or 'нет'}, "
        f"шаг {info['step']}, сид {info['seed']}"
    )
    if not info["ok"]:
        return f"{text}, ошибка [{info['error_code']}] {info['error_message']}"
    stats = info.get("stats")
    if stats:
        text += (
            f", строк {stats['input_rows']} -> {stats['output_rows']}, "
            f"диапазон {stats['distance_range_m']}, время {stats['calculation_time_ms']} мс"
        )
    return text


# --- построение ------------------------------------------------------------------------------


def run_build(job: MapJob) -> None:
    """Построение карты: PDF, PNG, архив и Excel."""
    out_rel = storage.job_results_rel(job.pk)
    try:
        params = params_from_job(job)
        source = book_path(job)
        storage.remove_tree(out_rel)  # результаты прежнего построения
        _clear_result_paths(job)
        out_dir = storage.resolve(out_rel)

        def progress(done: int, total: int) -> None:
            MapJob.objects.filter(pk=job.pk, state=State.BUILDING).update(
                progress_done=done, progress_total=total
            )

        # архив PNG не пишется: сайт собирает его при скачивании (листы не хранятся дважды)
        result = build_map(
            source,
            params,
            out_dir,
            progress=progress,
            sheets=book_sheets(job),
            zip_png=False,
        )
    except BuildError as exc:
        _finish_build_error(job, error_summary(_report_errors(exc)), out_rel)
        return
    except ParamsError as exc:
        _finish_build_error(job, f"Неверные параметры карты: {exc}", out_rel)
        return
    except Exception:
        logger.exception("Сбой построения, задание № %s", job.pk)
        _finish_build_error(
            job,
            "Внутренняя ошибка при построении карты. Попробуйте ещё раз или обратитесь "
            "к администратору.",
            out_rel,
        )
        return

    warnings = [i.format() for i in result.report.issues if i.level == WARNING]
    info = {
        "sections": len(result.sections),
        "pages": result.pages,
        "png_count": len(result.png_paths),
        "warnings": warnings[:MAX_BUILD_WARNINGS],
        "warnings_total": len(warnings),
        "timings": {k: round(v, 2) for k, v in result.timings.items()},
    }
    saved = MapJob.objects.filter(pk=job.pk, state=State.BUILDING).update(
        state=State.DONE,
        error_text="",
        pdf_path=f"{out_rel}/{PDF_NAME}",
        zip_path="",
        xlsx_path=f"{out_rel}/{XLSX_NAME}" if result.xlsx_path else "",
        png_dir=f"{out_rel}/{PNG_DIR}" if result.png_paths else "",
        result_info=info,
        built_at=timezone.now(),
        updated_at=timezone.now(),
    )
    MapJob.objects.filter(pk=job.pk, state=State.DONE).update(progress_done=result.pages)
    if not saved:  # карту удалили или вернули в очередь, пока она строилась
        storage.remove_tree(out_rel)
        return
    audit.log(
        AuditLog.Action.BUILD,
        user=job.owner,
        job=job,
        details=f"построено: листов {len(result.sections)}, время {result.total_time:.1f} с",
    )


def _report_errors(exc: BuildError) -> list[dict]:
    return [
        {"message": i.message, "cell": i.cell, "row": i.row}
        for i in exc.report.issues
        if i.level == ERROR
    ]


def _clear_result_paths(job: MapJob) -> None:
    MapJob.objects.filter(pk=job.pk).update(
        pdf_path="", zip_path="", xlsx_path="", png_dir="", result_info=None, built_at=None
    )


def _finish_build_error(job: MapJob, text: str, out_rel: str) -> None:
    MapJob.objects.filter(pk=job.pk, state=State.BUILDING).update(
        state=State.ERROR, error_text=text, updated_at=timezone.now()
    )
    _clear_result_paths(job)
    storage.remove_tree(out_rel)
    audit.log(AuditLog.Action.BUILD, user=job.owner, job=job, details=f"ошибка: {text}"[:500])
