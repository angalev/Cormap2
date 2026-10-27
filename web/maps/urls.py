from django.urls import path

from maps import views

urlpatterns = [
    path("", views.map_list, name="map_list"),
    path("forecast/", views.forecast, name="forecast"),
    path("maps/new/", views.map_new, name="map_new"),
    path("maps/template/", views.template_download, name="template_download"),
    path("maps/<int:pk>/", views.map_detail, name="map_detail"),
    path("maps/<int:pk>/status/", views.map_status, name="map_status"),
    path("maps/<int:pk>/split/", views.map_split, name="map_split"),
    path("maps/<int:pk>/build/", views.map_build, name="map_build"),
    path("maps/<int:pk>/cancel/", views.map_cancel, name="map_cancel"),
    path("maps/<int:pk>/rebuild/", views.map_rebuild, name="map_rebuild"),
    path("maps/<int:pk>/replace/", views.map_replace, name="map_replace"),
    path("maps/<int:pk>/delete/", views.map_delete, name="map_delete"),
    path("maps/<int:pk>/files/pdf/", views.map_file, {"kind": "pdf"}, name="map_file_pdf"),
    path("maps/<int:pk>/files/png-zip/", views.map_file, {"kind": "zip"}, name="map_file_zip"),
    path("maps/<int:pk>/files/xlsx/", views.map_file, {"kind": "xlsx"}, name="map_file_xlsx"),
    path(
        "maps/<int:pk>/preview/<int:section>/<int:sheet>/",
        views.map_preview,
        name="map_preview",
    ),
]
