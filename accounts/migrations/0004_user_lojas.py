from django.db import migrations, models


def preencher_lojas_permitidas(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.exclude(loja__isnull=True):
        user.lojas.add(user.loja)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_empresa"),
        ("cadastros", "0005_empresa_loja_empresa"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="lojas",
            field=models.ManyToManyField(blank=True, related_name="usuarios_permitidos", to="cadastros.loja"),
        ),
        migrations.RunPython(preencher_lojas_permitidas, migrations.RunPython.noop),
    ]
