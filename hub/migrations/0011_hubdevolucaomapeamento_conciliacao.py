from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hub", "0010_hubdevolucaofaixanumeracao")]

    operations = [
        migrations.AddField(
            model_name="hubdevolucaomapeamento",
            name="venda_troca_uuid",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="hubdevolucaomapeamento",
            name="operacao_uuid",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="hubdevolucaomapeamento",
            name="valor_consumido_offline",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True),
        ),
    ]
