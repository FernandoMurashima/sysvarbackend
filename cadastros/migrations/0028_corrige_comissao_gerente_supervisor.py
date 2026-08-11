from django.db import migrations


def permitir_comissao_gerente_supervisor(apps, schema_editor):
    Cargo = apps.get_model("cadastros", "Cargo")
    Cargo.objects.filter(codigo__in=["GERENTE", "SUPERVISOR"]).update(permite_comissao=True)


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0027_cargos_funcionarios_basicos"),
    ]

    operations = [
        migrations.RunPython(permitir_comissao_gerente_supervisor, migrations.RunPython.noop),
    ]
