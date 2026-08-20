from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0013_requisicaomaterialcategoria_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="requisicao",
            name="status",
            field=models.CharField(
                choices=[
                    ("RASCUNHO", "Não enviada"),
                    ("SOLICITADA", "Solicitada"),
                    ("EM_ANALISE", "Em análise"),
                    ("AGUARDANDO_APROVACAO", "Aguardando aprovação"),
                    ("DEVOLVIDA_CORRECAO", "Devolvida para correção"),
                    ("APROVADA", "Aprovada"),
                    ("EM_ATENDIMENTO", "Em atendimento"),
                    ("ATENDIDA_PARCIALMENTE", "Atendida parcialmente"),
                    ("EM_PROCESSO_COMPRA", "Em processo de compra"),
                    ("EM_PROCESSO_CONTRATACAO", "Em processo de contratação"),
                    ("CONCLUIDA", "Concluída"),
                    ("REJEITADA", "Rejeitada"),
                    ("CANCELADA", "Cancelada"),
                ],
                db_index=True,
                default="RASCUNHO",
                max_length=30,
            ),
        ),
    ]
