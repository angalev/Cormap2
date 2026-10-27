from django.contrib import admin

from maps.models import AuditLog, LoginAttempt, MapJob


@admin.register(MapJob)
class MapJobAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "object_name",
        "owner",
        "original_name",
        "state",
        "created_at",
        "built_at",
    )
    list_filter = ("state", "owner")
    search_fields = ("object_name", "original_name", "owner__username")
    date_hierarchy = "created_at"
    readonly_fields = (
        "created_at",
        "updated_at",
        "built_at",
        "viewed_at",
        "attempts",
        "params",
        "check_result",
        "split",
        "result_info",
        "source_path",
        "processed_path",
        "pdf_path",
        "zip_path",
        "xlsx_path",
        "png_dir",
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Журнал только для чтения."""

    list_display = ("created_at", "username", "action", "object_repr", "details", "ip")
    list_filter = ("action",)
    search_fields = ("username", "object_repr", "details", "ip")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("username", "ip", "failures", "window_start", "locked_until")
    search_fields = ("username", "ip")
