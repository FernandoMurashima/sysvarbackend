from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0012_alter_fornecedor_categoria"),
        ("produto", "0014_alter_produto_tipo_produto"),
    ]

    operations = [
        migrations.CreateModel(
            name="FichaTecnica",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("versao", models.CharField(default="1", max_length=20)),
                ("descricao", models.CharField(blank=True, max_length=120, null=True)),
                ("rendimento", models.DecimalField(decimal_places=3, default=1, max_digits=10)),
                ("status", models.CharField(choices=[("RASCUNHO", "Rascunho"), ("APROVADA", "Aprovada"), ("INATIVA", "Inativa")], db_index=True, default="RASCUNHO", max_length=15)),
                ("ativa", models.BooleanField(db_index=True, default=True)),
                ("observacoes", models.TextField(blank=True, null=True)),
                ("data_cadastro", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="fichas_tecnicas", to="cadastros.empresa")),
                ("produto_final", models.ForeignKey(limit_choices_to={"tipo_produto": "3"}, on_delete=django.db.models.deletion.PROTECT, related_name="fichas_tecnicas", to="produto.produto")),
            ],
        ),
        migrations.CreateModel(
            name="FichaTecnicaItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("INSUMO", "Insumo"), ("AVIAMENTO", "Aviamento"), ("SERVICO", "Serviço/Facção")], db_index=True, default="INSUMO", max_length=15)),
                ("descricao", models.CharField(blank=True, max_length=120, null=True)),
                ("quantidade", models.DecimalField(decimal_places=4, max_digits=12)),
                ("perda_percentual", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("custo_unitario_previsto", models.DecimalField(decimal_places=4, default=0, max_digits=12)),
                ("observacoes", models.CharField(blank=True, max_length=200, null=True)),
                ("ordem", models.PositiveIntegerField(default=1)),
                ("ficha", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="produto.fichatecnica")),
                ("fornecedor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="itens_ficha_tecnica", to="cadastros.fornecedor")),
                ("produto", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="itens_ficha_tecnica", to="produto.produto")),
                ("unidade", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="produto.unidade")),
            ],
            options={
                "ordering": ["ordem", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="fichatecnica",
            constraint=models.UniqueConstraint(fields=("empresa", "produto_final", "versao"), name="uq_empresa_produto_ficha_versao"),
        ),
        migrations.AddIndex(
            model_name="fichatecnica",
            index=models.Index(fields=["empresa", "produto_final"], name="produto_fic_empresa_08bd3c_idx"),
        ),
        migrations.AddIndex(
            model_name="fichatecnica",
            index=models.Index(fields=["status"], name="produto_fic_status_94d4df_idx"),
        ),
        migrations.AddIndex(
            model_name="fichatecnica",
            index=models.Index(fields=["ativa"], name="produto_fic_ativa_38b5bb_idx"),
        ),
        migrations.AddIndex(
            model_name="fichatecnicaitem",
            index=models.Index(fields=["ficha", "tipo"], name="produto_fic_ficha_i_0f08cd_idx"),
        ),
        migrations.AddIndex(
            model_name="fichatecnicaitem",
            index=models.Index(fields=["produto"], name="produto_fic_produto_d2fe09_idx"),
        ),
        migrations.AddIndex(
            model_name="fichatecnicaitem",
            index=models.Index(fields=["fornecedor"], name="produto_fic_fornece_93a9d6_idx"),
        ),
    ]
