from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_sessaousuario_sessiontoken_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usermodulepermission",
            name="modulo",
            field=models.CharField(
                choices=[
                    ("operacional", "Operacional"),
                    ("cadastros", "Cadastros"),
                    ("produtos", "Produtos"),
                    ("fiscal", "Fiscal"),
                    ("fiscal_contabil", "Fiscal e Contábil"),
                    ("estoque", "Estoque"),
                    ("distribuicao", "Distribuição"),
                    ("vendas", "Vendas"),
                    ("compras", "Compras"),
                    ("producao", "Produção"),
                    ("financeiro", "Financeiro"),
                    ("relatorios", "Relatórios"),
                    ("configuracoes", "Configurações"),
                    ("auditoria", "Auditoria"),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
    ]
