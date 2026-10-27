import django.utils.timezone
from django.db import migrations, models


def copy_created(apps, schema_editor):
    """У уже существующих карт книга загружена тогда же, когда создана карта."""
    MapJob = apps.get_model("maps", "MapJob")
    MapJob.objects.update(uploaded_at=models.F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("maps", "0006_auditlog_password_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="mapjob",
            name="uploaded_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now, verbose_name="книга загружена"
            ),
        ),
        migrations.RunPython(copy_created, migrations.RunPython.noop),
    ]
