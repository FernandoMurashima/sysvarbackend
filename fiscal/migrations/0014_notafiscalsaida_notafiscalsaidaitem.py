from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cadastros", "0016_empresa_licenca_master_empresa_usa_compras_and_more"),
        ("fiscal", "0013_popula_regras_tributarias_lucro_real_teste"),
        ("produto", "0026_popula_ncms_insumo_uso_consumo"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotaFiscalSaida",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo_operacao", models.CharField(choices=[("TRANSFERENCIA", "Transferência"), ("REMESSA", "Remessa"), ("VENDA", "Venda")], db_index=True, default="TRANSFERENCIA", max_length=20)),
                ("modelo", models.CharField(default="55", max_length=2)),
                ("serie", models.CharField(blank=True, default="", max_length=10)),
                ("numero", models.CharField(max_length=20)),
                ("documento_origem", models.CharField(blank=True, db_index=True, default="", max_length=50)),
                ("chave_acesso", models.CharField(blank=True, default="", max_length=60)),
                ("cfop", models.CharField(blank=True, default="", max_length=4)),
                ("natureza_operacao", models.CharField(blank=True, default="Transferência de produção", max_length=120)),
                ("status", models.CharField(choices=[("DI", "Digitada"), ("PR", "Pronta para emissão"), ("AU", "Autorizada"), ("CA", "Cancelada")], db_index=True, default="DI", max_length=2)),
                ("dt_emissao", models.DateField()),
                ("dt_saida", models.DateField()),
                ("valor_produtos", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("valor_desconto", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("valor_frete", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("valor_total", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("observacoes", models.CharField(blank=True, default="", max_length=255)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("criado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="nfe_saida_criadas", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="notas_saida", to="cadastros.empresa")),
                ("loja_destino", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="notas_saida_recebidas", to="cadastros.loja")),
                ("loja_origem", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="notas_saida_emitidas", to="cadastros.loja")),
                ("ordem_producao", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="notas_saida", to="produto.ordemproducao")),
            ],
            options={
                "db_table": "fiscal_nota_fiscal_saida",
            },
        ),
        migrations.CreateModel(
            name="NotaFiscalSaidaItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ean", models.CharField(db_index=True, max_length=13)),
                ("referencia", models.CharField(blank=True, default="", max_length=30)),
                ("descricao", models.CharField(max_length=120)),
                ("cor", models.CharField(blank=True, default="", max_length=80)),
                ("tamanho", models.CharField(blank=True, default="", max_length=30)),
                ("ncm", models.CharField(blank=True, default="", max_length=10)),
                ("cfop", models.CharField(blank=True, default="", max_length=4)),
                ("quantidade", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("valor_unitario", models.DecimalField(decimal_places=4, default=0, max_digits=12)),
                ("valor_desconto", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("valor_total", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("nota", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="fiscal.notafiscalsaida")),
                ("produto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="produto.produto")),
                ("sku", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="produto.produtodetalhe")),
            ],
            options={
                "db_table": "fiscal_nota_fiscal_saida_item",
            },
        ),
        migrations.AddConstraint(
            model_name="notafiscalsaida",
            constraint=models.UniqueConstraint(fields=("empresa", "modelo", "serie", "numero"), name="uq_fiscal_nfs_empresa_modelo_serie_numero"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaida",
            index=models.Index(fields=["empresa", "status"], name="ix_fiscal_nfs_empresa_status"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaida",
            index=models.Index(fields=["loja_origem", "dt_emissao"], name="ix_fiscal_nfs_origem_data"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaida",
            index=models.Index(fields=["loja_destino", "dt_saida"], name="ix_fiscal_nfs_destino_data"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaida",
            index=models.Index(fields=["ordem_producao"], name="ix_fiscal_nfs_op"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaidaitem",
            index=models.Index(fields=["nota"], name="ix_fiscal_nfs_item_nota"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaidaitem",
            index=models.Index(fields=["sku"], name="ix_fiscal_nfs_item_sku"),
        ),
        migrations.AddIndex(
            model_name="notafiscalsaidaitem",
            index=models.Index(fields=["ean"], name="ix_fiscal_nfs_item_ean"),
        ),
    ]
