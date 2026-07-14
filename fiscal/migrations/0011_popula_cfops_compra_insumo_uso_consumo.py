from django.db import migrations


def popular_cfops_compra_insumo_uso_consumo(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Cfop = apps.get_model("fiscal", "Cfop")
    cfops = [
        (
            "1101",
            "Compra para industrialização",
            "COMPRA",
            "DENTRO_UF",
            True,
            True,
            "Insumos e matérias-primas usados na produção.",
        ),
        (
            "2101",
            "Compra para industrialização",
            "COMPRA",
            "FORA_UF",
            True,
            True,
            "Insumos e matérias-primas usados na produção.",
        ),
        (
            "1556",
            "Compra de material para uso ou consumo",
            "COMPRA",
            "DENTRO_UF",
            True,
            True,
            "Materiais consumidos pela empresa, sem revenda direta.",
        ),
        (
            "2556",
            "Compra de material para uso ou consumo",
            "COMPRA",
            "FORA_UF",
            True,
            True,
            "Materiais consumidos pela empresa, sem revenda direta.",
        ),
    ]
    for empresa in Empresa.objects.all():
        for codigo, descricao, tipo, destino, estoque, financeiro, observacoes in cfops:
            Cfop.objects.update_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "tipo_operacao": tipo,
                    "destino": destino,
                    "movimenta_estoque": estoque,
                    "gera_financeiro": financeiro,
                    "ativo": True,
                    "observacoes": observacoes,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0010_cfop_cfop_uq_cfop_empresa_codigo"),
    ]

    operations = [
        migrations.RunPython(popular_cfops_compra_insumo_uso_consumo, migrations.RunPython.noop),
    ]
