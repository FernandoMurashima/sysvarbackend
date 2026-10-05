from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fiscal", "0042_reconcilia_sequencia_nfe_loja"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vendadevolucao",
            name="documento",
            field=models.CharField(db_index=True, max_length=50),
        ),
        migrations.AddConstraint(
            model_name="vendadevolucao",
            constraint=models.UniqueConstraint(fields=("empresa", "documento"), name="uq_devolucao_empresa_documento"),
        ),
    ]
