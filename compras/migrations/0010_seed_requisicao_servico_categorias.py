from django.db import migrations


def seed_categorias(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Categoria = apps.get_model("compras", "RequisicaoServicoCategoria")
    nomes = [
        "Ar-condicionado",
        "Eletrica",
        "Hidraulica",
        "Informatica",
        "Impressoras",
        "Moveis",
        "Equipamentos",
        "Seguranca",
        "Limpeza",
        "Pintura",
        "Outros",
    ]
    for empresa in Empresa.objects.all().only("pk"):
        for nome in nomes:
            Categoria.objects.get_or_create(empresa_id=empresa.pk, nome=nome)


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0009_requisicao_requisicaoservicocategoria_requisicaoitem_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_categorias, migrations.RunPython.noop),
    ]
