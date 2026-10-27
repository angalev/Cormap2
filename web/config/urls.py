"""Адреса сайта."""

from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.generic import RedirectView
from maps import views

# Админка входит через общую страницу входа (с ограничением попыток).
admin_login = login_not_required(
    RedirectView.as_view(url="/login/?next=/admin/", query_string=False)
)

urlpatterns = [
    path("admin/login/", admin_login),
    path("admin/", admin.site.urls),
    path("login/", views.SiteLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/", views.SitePasswordChangeView.as_view(), name="password_change"),
    path("", include("maps.urls")),
]

admin.site.site_header = "Коррозионные карты: администрирование"
admin.site.site_title = "Коррозионные карты"
admin.site.index_title = "Управление"
