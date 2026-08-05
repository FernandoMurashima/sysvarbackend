from django.db import migrations


def create_auditoria_module(apps, schema_editor):
    ModuloSistema = apps.get_model("cadastros", "ModuloSistema")
    ModuloSistema.objects.update_or_create(
        chave="auditoria",
        defaults={
            "nome": "Auditoria",
            "descricao": "Consulta central de logs de auditoria do SISVAR.",
            "categoria": "BASICO",
            "basico": True,
            "ativo": True,
            "ordem": 95,
            "dependencias": [],
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0019_empresacontrato_limite_sessoes_simultaneas"),
    ]

    operations = [
        migrations.RunPython(create_auditoria_module, migrations.RunPython.noop),
    ]
