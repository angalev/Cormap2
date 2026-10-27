"""Журнал входов: подключается в MapsConfig.ready()."""

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from maps import audit
from maps.models import AuditLog


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs) -> None:
    audit.log(AuditLog.Action.LOGIN, request=request, user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs) -> None:
    if user is not None:
        audit.log(AuditLog.Action.LOGOUT, request=request, user=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs) -> None:
    name = str(credentials.get("username", ""))
    audit.log(
        AuditLog.Action.LOGIN_FAILED, request=request, username=name, details="неверный пароль"
    )
