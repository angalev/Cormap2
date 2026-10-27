"""Загрузка книги и форма «Новая карта»."""

from __future__ import annotations

from conftest import FORM_DEFAULTS
from django.core.files.uploadedfile import SimpleUploadedFile
from maps import storage
from maps.models import AuditLog, MapJob


def _count_uploads() -> int:
    folder = storage.uploads_dir()
    return len(list(folder.glob("*"))) if folder.exists() else 0


def test_form_shows_defaults_and_extra_block(client, users, login):
    login(users["a"])
    html = client.get("/maps/new/").content.decode()
    assert 'value="-0,85"' in html
    assert 'value="-2,5"' in html
    assert 'value="0,8"' in html
    assert 'value="20"' in html and 'value="50"' in html
    assert "Дополнительные параметры" in html
    assert 'id="extra-params"' in html
    assert "Проверить" in html
    # никаких внешних ресурсов
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "https://" not in html


def test_upload_creates_job_with_generated_name(client, users, login, small_book, post_new):
    login(users["a"])
    response = post_new(small_book)
    assert response.status_code == 302
    job = MapJob.objects.get()
    assert response["Location"] == f"/maps/{job.pk}/"
    assert job.owner == users["a"]
    assert job.state == MapJob.State.UPLOADED
    assert job.original_name == "small.xlsx"
    assert job.source_path.startswith("uploads/") and job.source_path.endswith(".xlsx")
    assert "small" not in job.source_path  # имя на диске сгенерированное
    assert storage.resolve(job.source_path).is_file()
    assert job.params["u_min"] == -0.85 and job.params["split"] == "auto"
    assert AuditLog.objects.filter(action="upload", job=job).exists()


def test_comma_and_point_in_numbers(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(
        small_book,
        u_min="-0,9",
        u_max="-2.4",
        depth_min="0,75",
        diameter_mm="1 020,5",
        start_km="12,5",
        res_low="15,5",
        res_high="60",
    )
    params = MapJob.objects.get().params
    assert params["u_min"] == -0.9
    assert params["u_max"] == -2.4
    assert params["depth_min"] == 0.75
    assert params["diameter_mm"] == 1020.5
    assert params["start_km"] == 12.5
    assert params["res_low"] == 15.5


def test_empty_start_km_is_zero_and_diameter_optional(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, start_km="", diameter_mm="")
    params = MapJob.objects.get().params
    assert params["start_km"] == 0.0
    assert params["diameter_mm"] is None


def test_res_axis_max_optional_and_saved(client, users, login, small_book, post_new):
    login(users["a"])
    post_new(small_book, res_axis_max="")
    assert MapJob.objects.get().params["res_axis_max"] is None
    MapJob.objects.all().delete()
    post_new(small_book, res_axis_max="120,5")
    assert MapJob.objects.get().params["res_axis_max"] == 120.5


def test_res_axis_max_must_exceed_high_threshold(client, users, login, small_book, post_new):
    login(users["a"])
    for value in ("50", "30", "0"):  # res_high по умолчанию 50
        response = post_new(small_book, res_axis_max=value)
        assert response.status_code == 400, value
    response = post_new(small_book, res_axis_max="40")
    form = response.context["form"]
    assert "Верх оси УЭС" in form.errors["res_axis_max"][0]
    assert not MapJob.objects.exists()


def test_required_fields(client, users, login, small_book, post_new):
    login(users["a"])
    for field in ("object_name", "u_min", "u_max", "depth_min", "res_low", "res_high"):
        response = post_new(small_book, **{field: ""})
        assert response.status_code == 400, field
        assert "Обязательное поле" in response.content.decode(), field
    assert not MapJob.objects.exists()
    assert _count_uploads() == 0


def test_invalid_values(client, users, login, small_book, post_new):
    login(users["a"])
    cases = {
        "u_min": "abc",
        "depth_min": "-1",
        "res_low": "0",
        "diameter_mm": "-5",
        "paper": "A0",
        "legend": "xxx",
    }
    for field, value in cases.items():
        response = post_new(small_book, **{field: value})
        assert response.status_code == 400, field
    # не конечные числа
    assert post_new(small_book, u_min="inf").status_code == 400
    assert post_new(small_book, u_min="nan").status_code == 400
    # логика параметров ядра: макс. потенциал должен быть отрицательнее
    response = post_new(small_book, u_min="-2,5", u_max="-0,85")
    assert response.status_code == 400
    assert "отрицательнее" in response.content.decode()
    response = post_new(small_book, res_low="60", res_high="50")
    assert "Нижний порог УЭС" in response.content.decode()
    assert not MapJob.objects.exists()


def test_reject_not_xlsx_extension(client, users, login, post_new):
    login(users["a"])
    response = post_new(SimpleUploadedFile("данные.csv", b"a;b\n1;2\n"))
    assert response.status_code == 400
    assert "только файлы формата .xlsx" in response.content.decode()
    assert not MapJob.objects.exists()
    assert _count_uploads() == 0


def test_reject_renamed_text_file(client, users, login, post_new):
    login(users["a"])
    response = post_new(SimpleUploadedFile("книга.xlsx", "это просто текст".encode()))
    assert response.status_code == 400
    assert "не похож на книгу Excel" in response.content.decode()
    assert not MapJob.objects.exists()


def test_reject_zip_that_is_not_xlsx(client, users, login, post_new, tmp_path):
    import zipfile

    path = tmp_path / "x.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "привет")
    login(users["a"])
    response = post_new(SimpleUploadedFile("x.xlsx", path.read_bytes()))
    assert response.status_code == 400
    assert "нет нужных частей" in response.content.decode()


def test_reject_too_big(client, users, login, small_book, post_new, settings):
    settings.CORMAP_MAX_UPLOAD_BYTES = 1000
    login(users["a"])
    response = post_new(small_book)
    assert response.status_code == 400
    assert "Файл слишком большой" in response.content.decode()
    assert not MapJob.objects.exists()
    assert _count_uploads() == 0


def test_reject_too_big_request_before_parsing(client, users, login, small_book, settings):
    settings.CORMAP_MAX_UPLOAD_BYTES = 10
    settings.CORMAP_UPLOAD_OVERHEAD_BYTES = 10
    login(users["a"])
    data = {**FORM_DEFAULTS, "book": SimpleUploadedFile("a.xlsx", small_book.read_bytes())}
    response = client.post("/maps/new/", data)
    assert response.status_code == 400
    assert "Файл слишком большой" in response.content.decode()
    assert not MapJob.objects.exists()


def test_reject_empty_file(client, users, login, post_new):
    login(users["a"])
    response = post_new(SimpleUploadedFile("пусто.xlsx", b""))
    assert response.status_code == 400
    assert not MapJob.objects.exists()


def test_no_file(client, users, login):
    login(users["a"])
    response = client.post("/maps/new/", dict(FORM_DEFAULTS))
    assert response.status_code == 400
    assert not MapJob.objects.exists()


def test_ratio_errors_are_shown_at_field(client, users, login, small_book, post_new):
    login(users["a"])
    response = post_new(small_book, u_min="-2,5", u_max="-0,85")
    assert response.status_code == 400
    assert "отрицательнее" in response.context["form"].errors["u_max"][0]
    response = post_new(small_book, res_low="60", res_high="50")
    assert "Нижний порог УЭС" in response.context["form"].errors["res_low"][0]
    assert not MapJob.objects.exists()
