from django.db import migrations


def popular_ncms_insumo_uso_consumo(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Ncm = apps.get_model("produto", "Ncm")
    ncms = [
        (
            "5407.61.00",
            "Tecidos de filamentos sintéticos de poliéster, contendo 85% ou mais em peso de poliéster não texturizado",
            "TECIDO",
            "18.00",
        ),
        (
            "4821.10.00",
            "Etiquetas de papel ou cartão, impressas",
            "EMBALAGEM",
            "18.00",
        ),
    ]
    for empresa in Empresa.objects.all():
        for ncm, descricao, categoria, aliquota in ncms:
            Ncm.objects.update_or_create(
                empresa=empresa,
                ncm=ncm,
                defaults={
                    "descricao": descricao,
                    "categoria": categoria,
                    "aliquota": aliquota,
                    "ativo": True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0025_rename_produto_fic_empresa_08bd3c_idx_produto_fic_empresa_89ff80_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(popular_ncms_insumo_uso_consumo, migrations.RunPython.noop),
    ]
