from django.db import migrations, models
import django.db.models.deletion


def vincular_empresa_usuarios(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Empresa = apps.get_model("cadastros", "Empresa")

    empresa_padrao = Empresa.objects.order_by("id").first()
    for user in User.objects.select_related("loja").all():
        empresa = getattr(user.loja, "empresa", None) or empresa_padrao
        if empresa:
            user.empresa = empresa
            user.save(update_fields=["empresa"])


def desvincular_empresa_usuarios(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.update(empresa=None)


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0005_empresa_loja_empresa"),
        ("accounts", "0002_alter_user_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="usuarios",
                to="cadastros.empresa",
            ),
        ),
        migrations.RunPython(vincular_empresa_usuarios, desvincular_empresa_usuarios),
    ]
