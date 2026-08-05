from django.core.exceptions import ValidationError
from django.db import migrations, models
import django.db.models.deletion


def validar_lojas_com_empresa(apps, schema_editor):
    Loja = apps.get_model("cadastros", "Loja")
    ids = list(Loja.objects.filter(empresa__isnull=True).values_list("pk", flat=True))
    if ids:
        raise ValidationError(
            "Existem lojas sem empresa; execute o saneamento antes de aplicar empresa obrigatória. "
            f"IDs: {ids}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0021_empresacontrato_motivo_suspensao_and_more"),
    ]

    operations = [
        migrations.RunPython(validar_lojas_com_empresa, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="loja",
            name="empresa",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lojas",
                to="cadastros.empresa",
            ),
        ),
    ]
