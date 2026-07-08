from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0014_rename_financeiro__empresa_1e920c_idx_financeiro__empresa_c8fd7f_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movimentacaofinanceira",
            name="origem",
            field=models.CharField(
                choices=[
                    ("MANUAL", "Manual"),
                    ("PAGAR", "Contas a pagar"),
                    ("RECEBER", "Contas a receber"),
                    ("TRANSFERENCIA", "Transferência entre caixas"),
                    ("CARTAO", "Recebível de cartão"),
                    ("ANTECIPACAO", "Antecipação de recebíveis"),
                    ("CMV", "Custo da mercadoria vendida"),
                    ("COMISSAO", "Comissão de venda"),
                ],
                db_index=True,
                default="MANUAL",
                max_length=15,
            ),
        ),
    ]
