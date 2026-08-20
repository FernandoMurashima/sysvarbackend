from django.db import migrations


REQUISICOES_MODULES = [
    "requisicoes",
    "requisicoes_analise",
    "requisicoes_atendimento",
    "requisicoes_todas",
]


def clean_copied_requisicoes_permissions(apps, schema_editor):
    UserModulePermission = apps.get_model("accounts", "UserModulePermission")
    PerfilModuloPermissao = apps.get_model("accounts", "PerfilModuloPermissao")

    UserModulePermission.objects.filter(
        modulo__in=REQUISICOES_MODULES,
    ).exclude(
        user__is_superuser=True,
    ).exclude(
        user__type="Admin",
    ).delete()

    PerfilModuloPermissao.objects.filter(
        modulo__chave__in=REQUISICOES_MODULES,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_requisicoes_module_permissions"),
    ]

    operations = [
        migrations.RunPython(clean_copied_requisicoes_permissions, migrations.RunPython.noop),
    ]
