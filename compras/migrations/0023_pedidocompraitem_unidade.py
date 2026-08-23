from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0022_cotacaoproposta_forma_pagamento"),
        ("produto", "0032_grade_uq_empresa_grade_descricao_aux_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pedidocompraitem",
            name="unidade",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="itens_pedido_compra",
                to="produto.unidade",
            ),
        ),
    ]
