"""Представления сайта. Все, кроме входа, требуют авторизации (LoginRequiredMiddleware).

Чужая карта - 404 (администратор видит все карты, но менять чужие не может).
Путь к файлу никогда не берётся из запроса: только из записи MapJob и номера листа.
"""

from __future__ import annotations

import os
import tempfile
import uuid
import zipfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from PIL import Image

from cormap.layout import LayoutError
from cormap.layout_settings import load_settings
from cormap.model import ERROR, WARNING
from cormap.params import ParamsError
from cormap.serialize import objects_from_list, resplit
from cormap.template import build_template
from maps import audit, pending, storage, throttle
from maps.forms import (
    NewMapForm,
    ReplaceBookForm,
    SplitForm,
    bounds_km_text,
    split_to_km_text,
)
from maps.models import AuditLog, MapJob
from maps.services import (
    make_split_state,
    params_from_job,
    params_to_dict,
    png_name,
    refinery_dict,
    requeue_if_missing,
    with_new_seed,
)

State = MapJob.State

RECORDS_PER_PAGE = 100  # строк таблицы распознавания на странице
SHOWN_ISSUES = 200  # замечаний на странице (остальные - счётчиком)
SHOWN_ATTENTION = 200  # нераспознанных пометок в верхнем списке
SECTIONS_PER_PAGE = 6  # участков (по два листа) на странице предпросмотра
THUMB_WIDTH = 1000  # ширина уменьшенного предпросмотра, px
PENDING_NEW = "new"  # ключ отложенной книги формы «Новая карта» (maps/pending.py)


# --- вспомогательное --------------------------------------------------------------------


def get_job(request: HttpRequest, pk: int, *, owner_only: bool = False) -> MapJob:
    """Карта пользователя; чужая - 404. Администратор видит все (но не меняет чужие)."""
    qs = MapJob.objects.select_related("owner")
    if owner_only or not request.user.is_staff:
        qs = qs.filter(owner=request.user)
    return get_object_or_404(qs, pk=pk)


def _touch(job: MapJob) -> None:
    """Отмечает, что карту открывали (не чаще раза в сутки): по этой отметке решается,
    когда удалять её готовые файлы (ТЗ, раздел 9)."""
    now = timezone.now()
    if job.viewed_at is None or now - job.viewed_at > timedelta(days=1):
        MapJob.objects.filter(pk=job.pk).update(viewed_at=now)
        job.viewed_at = now


def _save_upload(upload) -> str:
    """Сохраняет загруженный файл под сгенерированным именем; возвращает относительный путь."""
    rel = storage.new_upload_name()
    path = storage.resolve(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    return rel


def _attached_book(request: HttpRequest, form, key: str) -> dict[str, Any] | None:
    """Книга, отложенная при прошлой отправке формы (maps/pending.py).

    Если с формой не пришёл новый файл, а отложенный есть, поле книги необязательно.
    """
    if request.FILES.get("book"):
        return None
    attached = pending.get(request, key)
    if attached:
        form.fields["book"].required = False
    return attached


def _store_book(request: HttpRequest, form, key: str) -> tuple[str, str, int] | None:
    """Книга проверенной формы -> загрузки: (путь, исходное имя, размер).

    Берётся новый файл, а без него - отложенный. None - отложенный файл пропал
    (ошибка добавлена в форму).
    """
    upload = form.cleaned_data.get("book")
    if upload:
        pending.drop(request, key)
        return _save_upload(upload), Path(upload.name).name[:255], upload.size
    taken = pending.take(request, key)
    if taken is None:
        form.add_error("book", "Прикреплённый файл не найден, выберите его заново.")
    return taken


def _keep_book(request: HttpRequest, form, key: str) -> dict[str, Any] | None:
    """Форма не прошла проверку: годную книгу откладываем, чтобы не выбирать её заново."""
    upload = request.FILES.get("book")
    if upload is None:
        return pending.get(request, key)
    if "book" in form.errors:  # новый файл негоден: прежний отложенный тоже не оставляем
        pending.drop(request, key)
        return None
    return pending.keep(request, key, upload)


def _too_big_request(request: HttpRequest) -> bool:
    """Тело запроса заведомо больше предела (проверка до разбора multipart)."""
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return False
    return length > settings.CORMAP_MAX_UPLOAD_BYTES + settings.CORMAP_UPLOAD_OVERHEAD_BYTES


def _too_big_message() -> str:
    return (
        f"Файл слишком большой: допустимо не более "
        f"{settings.CORMAP_MAX_UPLOAD_BYTES / 1024 / 1024:g} МБ."
    )


# --- вход ---------------------------------------------------------------------------------


@method_decorator(login_not_required, name="dispatch")
class SiteLoginView(LoginView):
    """Вход с ограничением числа попыток: по паре «логин + IP» и по логину
    (maps/throttle.py)."""

    template_name = "maps/login.html"
    redirect_authenticated_user = True

    def _posted_name(self) -> str:
        return throttle.clean_name(self.request.POST.get("username", ""))

    def _locked(self, username: str, minutes: int) -> HttpResponse:
        form = AuthenticationForm(self.request, initial={"username": username})
        context = self.get_context_data(form=form)
        context["lock_message"] = (
            "Слишком много неудачных попыток входа. Вход заблокирован, "
            f"попробуйте через {minutes} мин."
        )
        return self.render_to_response(context, status=429)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        username = self._posted_name()
        minutes = throttle.blocked_minutes(username, audit.client_ip(request))
        if minutes:
            audit.log(
                AuditLog.Action.LOGIN_FAILED,
                request=request,
                username=username,
                details="вход заблокирован: слишком много попыток",
            )
            return self._locked(username, minutes)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        username = self._posted_name()
        ip = audit.client_ip(self.request)
        throttle.register_failure(username, ip)
        minutes = throttle.blocked_minutes(username, ip)
        if minutes:
            return self._locked(username, minutes)
        return super().form_invalid(form)

    def form_valid(self, form):
        throttle.reset(form.get_user().get_username(), audit.client_ip(self.request))
        throttle.reset(self._posted_name(), audit.client_ip(self.request))
        return super().form_valid(form)


class SitePasswordChangeView(PasswordChangeView):
    """Смена своего пароля (нужен текущий). Остальные сеансы пользователя Django
    завершает сам: их подпись зависит от хеша пароля."""

    template_name = "maps/password_change.html"
    success_url = reverse_lazy("map_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.log(AuditLog.Action.PASSWORD, request=self.request)
        messages.success(self.request, "Пароль изменён.")
        return response


# --- список, создание, удаление ---------------------------------------------------------


@require_GET
def map_list(request: HttpRequest) -> HttpResponse:
    jobs = MapJob.objects.select_related("owner")
    if not request.user.is_staff:
        jobs = jobs.filter(owner=request.user)
    context = {"jobs": jobs, "keep_days": max(settings.CORMAP_MAP_KEEP_DAYS, 0)}
    return render(request, "maps/map_list.html", context)


@require_http_methods(["GET", "POST"])
def map_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        if _too_big_request(request):
            context = {
                "form": NewMapForm(),
                "upload_error": _too_big_message(),
                "attached_book": pending.get(request, PENDING_NEW),
            }
            return render(request, "maps/map_new.html", context, status=400)
        form = NewMapForm(request.POST, request.FILES)
        _attached_book(request, form, PENDING_NEW)
        book = _store_book(request, form, PENDING_NEW) if form.is_valid() else None
        if book:
            rel, name, size = book
            params = form.build_params()
            options = form.refine_options()
            job = MapJob.objects.create(
                owner=request.user,
                object_name=params.object_name,
                params=params_to_dict(params, with_new_seed(options) if options else None),
                original_name=name,
                source_path=rel,
                state=State.UPLOADED,
            )
            audit.log(
                AuditLog.Action.UPLOAD,
                request=request,
                job=job,
                details=f"файл «{job.original_name}», {size} байт",
            )
            return redirect("map_detail", pk=job.pk)
        context = {"form": form, "attached_book": _keep_book(request, form, PENDING_NEW)}
        return render(request, "maps/map_new.html", context, status=400)
    pending.drop(request, PENDING_NEW)  # форма открыта заново: прежний файл не прикреплён
    return render(request, "maps/map_new.html", {"form": NewMapForm()})


@require_GET
def template_download(request: HttpRequest) -> HttpResponse:
    """Шаблон книги Excel (ТЗ, 4.6): собирается при скачивании, в базу ничего не пишется."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shablon_knigi.xlsx"
        build_template(path, "Template")
        content = path.read_bytes()
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="shablon_knigi.xlsx"'
    response["Cache-Control"] = "private, no-store"
    return response


@require_http_methods(["GET", "POST"])
def map_delete(request: HttpRequest, pk: int) -> HttpResponse:
    # удалять можно только свою карту; в любом состоянии: задание, которое в это время
    # выполняется, обработчик остановит сам (ТЗ, раздел 9)
    job = get_job(request, pk, owner_only=True)
    if request.method == "POST":
        description = f"{job.object_name} (№ {job.pk}), файл «{job.original_name}»"
        audit.log(
            AuditLog.Action.DELETE,
            request=request,
            job=job,
            object_repr=str(job),
            details=description,
        )
        job.delete()  # файлы удаляет обработчик post_delete
        messages.success(request, "Карта удалена.")
        return redirect("map_list")
    return render(request, "maps/map_confirm_delete.html", {"job": job})


# --- страница карты ----------------------------------------------------------------------


@require_GET
def map_detail(request: HttpRequest, pk: int) -> HttpResponse:
    job = get_job(request, pk)
    _touch(job)
    # готовая карта без файлов (давно не открывали или восстановлена из копии) строится заново
    if requeue_if_missing(job):
        job.refresh_from_db()
    if job.state in MapJob.CHECK_STATES:
        return render(request, "maps/map_check.html", _check_context(request, job))
    return render(request, "maps/map_done.html", _map_context(request, job))


def _is_owner(request: HttpRequest, job: MapJob) -> bool:
    return job.owner_id == request.user.pk


def _split_form(job: MapJob, params, data=None, initial=None) -> SplitForm:
    """Форма правки разбиения с километражем карты (для сообщений и подсказок)."""
    result = job.check_result or {}
    dist_range = result.get("dist_range")
    return SplitForm(
        data,
        initial=initial,
        start_km=params.start_km,
        dist_range=(float(dist_range[0]), float(dist_range[1])) if dist_range else None,
        max_sections=load_settings().max_sections,
    )


def _check_context(
    request: HttpRequest, job: MapJob, split_form: SplitForm | None = None
) -> dict[str, Any]:
    result = job.check_result or {}
    issues = result.get("issues", [])
    errors = [i for i in issues if i["level"] == ERROR]
    warnings = [i for i in issues if i["level"] == WARNING]
    notes = result.get("notes") or {}
    records = notes.get("records", [])
    bad_records = [r for r in records if not r["recognized"]]
    page = Paginator(records, RECORDS_PER_PAGE).get_page(request.GET.get("rp"))
    split = job.split or {}
    suppressed = result.get("suppressed", {})
    context: dict[str, Any] = {
        "job": job,
        "is_owner": _is_owner(request, job),
        "result": result,
        "blocks": result.get("blocks", []),
        "errors": errors[:SHOWN_ISSUES],
        "warnings": warnings[: max(SHOWN_ISSUES - len(errors), 20)],
        "errors_total": len(errors) + suppressed.get(ERROR, 0),
        "warnings_total": len(warnings) + suppressed.get(WARNING, 0),
        "notes": notes,
        "bad_records": bad_records[:SHOWN_ATTENTION],
        "bad_records_total": len(bad_records),
        "unpaired": notes.get("unpaired_issues", []),
        "records_page": page,
        "records_total": len(records),
        "split": split,
        "split_issues": split.get("issues", []),
        "can_build": job.state == State.CHECKED and bool(split),
    }
    if split_form is None and job.state == State.CHECKED and split:
        try:
            params = params_from_job(job)
            # в режиме «авто» поле пустое; текущие границы показываются отдельной строкой
            split_form = _split_form(job, params, initial=_split_initial(params))
            context["current_bounds"] = bounds_km_text(split.get("bounds_m", []), params.start_km)
        except ParamsError:
            split_form = SplitForm(initial={"mode": "auto"})
    elif split_form is not None and split.get("bounds_m"):
        context["current_bounds"] = bounds_km_text(split["bounds_m"], split_form.start_km)
    context["split_form"] = split_form
    return context


def _split_initial(params) -> dict[str, str]:
    mode, text = split_to_km_text(params.split, params.start_km)
    return {"mode": mode, "value": text}


def _map_context(request: HttpRequest, job: MapJob) -> dict[str, Any]:
    context: dict[str, Any] = {
        "job": job,
        "is_owner": _is_owner(request, job),
        "info": job.result_info or {},
        "can_rebuild": bool(job.check_result and job.split),
    }
    if job.state == State.DONE and job.png_dir:
        total = job.sections_count
        page = Paginator(range(1, total + 1), SECTIONS_PER_PAGE).get_page(request.GET.get("page"))
        context["preview_page"] = page
        context["preview_sections"] = list(page.object_list)
    return context


# --- действия над картой --------------------------------------------------------------------


@require_POST
def map_split(request: HttpRequest, pk: int) -> HttpResponse:
    """«Пересчитать разбиение»: по сохранённым объектам, без перечитывания книги."""
    job = get_job(request, pk, owner_only=True)
    if job.state != State.CHECKED or not job.check_result:
        messages.error(request, "Разбиение можно менять только у проверенной карты.")
        return redirect("map_detail", pk=job.pk)
    params = params_from_job(job)
    form = _split_form(job, params, request.POST)
    if form.is_valid():
        spec = form.cleaned_data["split"]
        try:
            new_params = replace(params, split=spec)
            result = resplit(
                objects_from_list(job.check_result.get("objects", [])),
                tuple(job.check_result["dist_range"])
                if job.check_result.get("dist_range")
                else None,
                new_params.split_text,
                new_params.paper,
                new_params.start_km,
            )
        except (LayoutError, ParamsError) as exc:
            form.add_error("value", str(exc))
        else:
            data = params_to_dict(new_params, refinery_dict(job))  # настройки модуля сохраняем
            changed = MapJob.objects.filter(pk=job.pk, state=State.CHECKED).update(
                params=data,
                split=make_split_state(result, new_params.split),
                updated_at=timezone.now(),
            )
            if changed:
                messages.success(request, "Разбиение пересчитано.")
            return redirect("map_detail", pk=job.pk)
    return render(request, "maps/map_check.html", _check_context(request, job, form), status=400)


@require_POST
def map_build(request: HttpRequest, pk: int) -> HttpResponse:
    """«Построить карту»: ставит задание в очередь (атомарно, только из состояния checked)."""
    job = get_job(request, pk, owner_only=True)
    if not job.check_result or not job.split or not job.check_result.get("ok", False):
        messages.error(request, "Построение недоступно: проверка не пройдена.")
        return redirect("map_detail", pk=job.pk)
    queued = MapJob.objects.filter(pk=job.pk, state=State.CHECKED).update(
        state=State.QUEUED,
        attempts=0,
        error_text="",
        progress_done=0,
        progress_total=0,
        updated_at=timezone.now(),
    )
    if queued:
        audit.log(
            AuditLog.Action.BUILD,
            request=request,
            job=job,
            details=f"поставлено в очередь, листов: {job.split.get('count', '?')}",
        )
    else:
        messages.error(request, "Построение можно запустить только у проверенной карты.")
    return redirect("map_detail", pk=job.pk)


CANCEL_CHECK_TEXT = "Проверка отменена пользователем."


@require_POST
def map_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """«Отменить»: снимает проверку или построение, которое ждёт очереди или выполняется.

    Меняется только состояние в базе; выполняющееся задание обработчик остановит сам,
    когда увидит, что состояние уже не то (maps/worker.py, run_isolated).
    """
    job = get_job(request, pk, owner_only=True)
    now = timezone.now()
    checks = MapJob.objects.filter(pk=job.pk, state__in=(State.UPLOADED, State.CHECKING)).update(
        state=State.CHECK_FAILED,
        check_result=None,
        split=None,
        error_text=CANCEL_CHECK_TEXT,
        updated_at=now,
    )
    builds = 0
    if not checks:
        # построение начинается только с проверенной карты: к ней и возвращаемся
        builds = MapJob.objects.filter(pk=job.pk, state__in=(State.QUEUED, State.BUILDING)).update(
            state=State.CHECKED, error_text="", progress_done=0, progress_total=0, updated_at=now
        )
    if checks:
        audit.log(AuditLog.Action.CHECK, request=request, job=job, details="отменено пользователем")
        messages.success(request, "Проверка отменена.")
    elif builds:
        audit.log(AuditLog.Action.BUILD, request=request, job=job, details="отменено пользователем")
        messages.success(request, "Построение отменено.")
    else:
        messages.error(request, "Отменять нечего: карта не проверяется и не строится.")
    return redirect("map_detail", pk=job.pk)


@require_POST
def map_rebuild(request: HttpRequest, pk: int) -> HttpResponse:
    """«Перестроить с другим разбиением»: возврат к шагу «Проверка и разбиение»."""
    job = get_job(request, pk, owner_only=True)
    if job.check_result and job.split and job.state in (State.DONE, State.ERROR):
        MapJob.objects.filter(pk=job.pk, state=job.state).update(
            state=State.CHECKED, error_text="", updated_at=timezone.now()
        )
    else:
        messages.error(request, "Вернуться к разбиению нельзя: нет результата проверки.")
    return redirect("map_detail", pk=job.pk)


@require_http_methods(["GET", "POST"])
def map_replace(request: HttpRequest, pk: int) -> HttpResponse:
    """«Загрузить исправленную книгу»: заменяет книгу и запускает проверку заново."""
    job = get_job(request, pk, owner_only=True)
    if job.state not in (State.CHECK_FAILED, State.CHECKED, State.ERROR):
        messages.error(request, "Книгу можно заменить после проверки или ошибки.")
        return redirect("map_detail", pk=job.pk)
    key = f"replace:{job.pk}"
    if request.method == "POST":
        if _too_big_request(request):
            context = {
                "job": job,
                "form": ReplaceBookForm(initial=refinery_dict(job)),
                "upload_error": _too_big_message(),
                "attached_book": pending.get(request, key),
            }
            return render(request, "maps/map_replace.html", context, status=400)
        form = ReplaceBookForm(request.POST, request.FILES, initial=refinery_dict(job))
        _attached_book(request, form, key)
        book = _store_book(request, form, key) if form.is_valid() else None
        if book:
            new_rel, name, size = book
            old_rel = job.source_path
            old_processed = job.processed_path
            params = params_from_job(job)
            options = form.refine_options()
            # без блока на форме (шаг выключен) прежние настройки карты остаются как были
            refinery = with_new_seed(options) if options else refinery_dict(job)
            data = params_to_dict(replace(params, split="auto"), refinery)
            with transaction.atomic():
                changed = MapJob.objects.filter(pk=job.pk, state=job.state).update(
                    state=State.UPLOADED,
                    attempts=0,
                    source_path=new_rel,
                    uploaded_at=timezone.now(),
                    processed_path="",
                    original_name=name,
                    params=data,
                    check_result=None,
                    split=None,
                    error_text="",
                    updated_at=timezone.now(),
                )
            if not changed:
                storage.remove_file(new_rel)
                messages.error(request, "Состояние карты изменилось, повторите действие.")
                return redirect("map_detail", pk=job.pk)
            storage.remove_file(old_rel)
            storage.remove_file(old_processed)
            audit.log(
                AuditLog.Action.UPLOAD,
                request=request,
                job=job,
                details=f"замена книги: файл «{name}», {size} байт",
            )
            return redirect("map_detail", pk=job.pk)
        context = {"job": job, "form": form, "attached_book": _keep_book(request, form, key)}
        return render(request, "maps/map_replace.html", context, status=400)
    pending.drop(request, key)  # форма открыта заново: прежний файл не прикреплён
    form = ReplaceBookForm(initial=refinery_dict(job))
    return render(request, "maps/map_replace.html", {"job": job, "form": form})


# --- состояние (опрос из браузера) --------------------------------------------------------------


@never_cache
@require_GET
def map_status(request: HttpRequest, pk: int) -> JsonResponse:
    job = get_job(request, pk)
    return JsonResponse(
        {
            "state": job.state,
            "status": job.status_text,
            "in_progress": job.in_progress,
            "stalled": job.stalled,
            "progress_done": job.progress_done,
            "progress_total": job.progress_total,
            "error": job.error_text,
        }
    )


# --- файлы -----------------------------------------------------------------------------------

FILE_KINDS = {
    "pdf": ("pdf_path", "application/pdf", "karta_{pk}.pdf"),
    # архив PNG не хранится: собирается из листов при скачивании
    "zip": ("png_dir", "application/zip", "karta_{pk}_png.zip"),
    "xlsx": (
        "xlsx_path",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "karta_{pk}.xlsx",
    ),
}


def _png_archive(folder: Path):
    """Архив листов PNG во временном файле (без сжатия: PNG уже сжаты)."""
    files = sorted(folder.glob("list_*.png")) if folder.is_dir() else []
    if not files:
        raise Http404("Файл не найден.")
    tmp = tempfile.TemporaryFile()  # noqa: SIM115  (закрывает FileResponse)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for item in files:
            zf.write(item, item.name)
    tmp.seek(0)
    return tmp


@require_GET
def map_file(request: HttpRequest, pk: int, kind: str) -> HttpResponse:
    """PDF, архив PNG или Excel готовой карты (путь - только из записи в базе)."""
    job = get_job(request, pk)
    spec = FILE_KINDS.get(kind)
    if spec is None or job.state != State.DONE:
        raise Http404("Файл не найден.")
    if job.results_missing:  # файлы удалены: страница карты запустит построение
        return redirect("map_detail", pk=job.pk)
    attr, content_type, name = spec
    path = storage.resolve(getattr(job, attr))
    if kind == "zip":
        content = _png_archive(path)
    elif path.is_file():
        content = open(path, "rb")  # noqa: SIM115  (закрывает FileResponse)
    else:
        raise Http404("Файл не найден.")
    _touch(job)
    audit.log(
        AuditLog.Action.DOWNLOAD,
        request=request,
        job=job,
        details=f"{kind}: {name.format(pk=job.pk)}",
    )
    return FileResponse(
        content,
        as_attachment=True,
        filename=name.format(pk=job.pk),
        content_type=content_type,
    )


def _thumbnail(source: Path, thumb: Path) -> Path:
    """Уменьшенная копия листа (кэшируется рядом с результатами карты)."""
    if thumb.is_file() and thumb.stat().st_mtime >= source.stat().st_mtime:
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    tmp = thumb.with_name(f"{thumb.stem}.{uuid.uuid4().hex}.tmp")
    with Image.open(source) as img:
        ratio = THUMB_WIDTH / img.width
        small = img.convert("RGB").resize(
            (THUMB_WIDTH, max(1, round(img.height * ratio))), Image.Resampling.LANCZOS
        )
        small.save(tmp, format="PNG", optimize=False)
    os.replace(tmp, thumb)
    return thumb


@require_GET
def map_preview(request: HttpRequest, pk: int, section: int, sheet: int) -> FileResponse:
    """PNG листа карты: по умолчанию уменьшенный, ``?full=1`` - полный размер."""
    job = get_job(request, pk)
    total = job.sections_count
    if job.state != State.DONE or not job.png_dir or sheet not in (1, 2):
        raise Http404("Лист не найден.")
    if not 1 <= section <= total:
        raise Http404("Лист не найден.")
    name = png_name(section, sheet, total)  # имя строится по номерам, не берётся из запроса
    source = storage.resolve(job.png_dir) / name
    if not source.is_file():
        raise Http404("Лист не найден.")
    path = source
    if request.GET.get("full") != "1":
        path = _thumbnail(source, storage.resolve(job.png_dir).parent / "thumbs" / name)
    response = FileResponse(open(path, "rb"), content_type="image/png")  # noqa: SIM115
    response["Cache-Control"] = "private, max-age=300"
    return response


FORECAST_LOG_KINDS = {"calc": "расчёт на странице", "download": "скачивание протокола"}


@require_http_methods(["GET", "POST"])
def forecast(request: HttpRequest) -> HttpResponse:
    """Калькулятор ЭХЗ (ТЗ, 9.1): считает браузер, сюда приходит только отметка в журнал.

    В журнал пишется факт расчёта: ни введённых данных, ни результата сайт не получает.
    """
    if request.method == "GET":
        return render(request, "maps/calculator.html")
    details = FORECAST_LOG_KINDS.get(request.POST.get("kind", ""))
    if details is None:
        return HttpResponse(status=400)
    audit.log(AuditLog.Action.FORECAST, request=request, details=details)
    return HttpResponse(status=204)
