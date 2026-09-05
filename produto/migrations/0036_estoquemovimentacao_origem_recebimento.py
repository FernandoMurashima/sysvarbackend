from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produto", "0035_estoquemovimentacao_origem"),
    ]

    operations = [
        migrations.AlterField(
            model_name="estoquemovimentacao",
            name="origem",
            field=models.CharField(blank=True, choices=[("NFE", "NF-e"), ("VENDA", "Venda"), ("DEVOLUCAO", "Devolução"), ("TRANSFERENCIA", "Transferência"), ("INVENTARIO", "Inventário"), ("PRODUCAO", "Produção"), ("AJUSTE_MANUAL", "Ajuste manual"), ("RECEBIMENTO", "Recebimento de mercadoria")], db_index=True, default="", max_length=20),
        ),
    ]
