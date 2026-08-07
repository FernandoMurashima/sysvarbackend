from django.conf import settings
from django.db import migrations, models
import django.utils.timezone
import django.db.models.deletion
import cadastros.validators


def only_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def migrar_fornecedores(apps, schema_editor):
    Fornecedor = apps.get_model("cadastros", "Fornecedor")
    FornecedorCategoria = apps.get_model("cadastros", "FornecedorCategoria")
    sem_empresa = list(Fornecedor.objects.filter(empresa__isnull=True).values_list("id", flat=True)[:20])
    if sem_empresa:
        raise RuntimeError(
            "Existem fornecedores sem empresa. Regularize estes registros antes desta migration. "
            f"Exemplos: {sem_empresa}"
        )
    for fornecedor in Fornecedor.objects.all().iterator():
        doc = only_digits(getattr(fornecedor, "documento", None) or getattr(fornecedor, "cnpj", None))
        update_fields = []
        if doc:
            fornecedor.documento = doc
            update_fields.append("documento")
            if len(doc) == 14:
                fornecedor.tipo_pessoa = "PJ"
                fornecedor.cnpj = doc
                update_fields.extend(["tipo_pessoa", "cnpj"])
            elif len(doc) == 11:
                fornecedor.tipo_pessoa = "PF"
                update_fields.append("tipo_pessoa")
        if update_fields:
            fornecedor.save(update_fields=sorted(set(update_fields)))
        categoria = getattr(fornecedor, "categoria", None)
        if categoria:
            FornecedorCategoria.objects.get_or_create(
                fornecedor_id=fornecedor.pk,
                empresa_id=fornecedor.empresa_id,
                categoria=categoria,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0023_cliente_multiempresa_documento_padrao"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="fornecedor",
            name="uq_empresa_fornecedor_cnpj",
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="tipo_pessoa",
            field=models.CharField(choices=[("PF", "Pessoa física"), ("PJ", "Pessoa jurídica")], db_index=True, default="PJ", max_length=2),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="documento",
            field=models.CharField(blank=True, db_index=True, max_length=14, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="motivo_bloqueio",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="observacao_bloqueio",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="bloqueado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="bloqueado_por",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="fornecedores_bloqueados", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="inscricao_estadual",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="inscricao_municipal",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="contribuinte_icms",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="site",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="prazo_padrao_pagamento",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="observacoes_comerciais",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="natureza_padrao",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="fornecedores_padrao", to="cadastros.nat_lancamento"),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="banco",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="agencia",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="conta",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="tipo_conta",
            field=models.CharField(blank=True, choices=[("CORRENTE", "Conta corrente"), ("POUPANCA", "Conta poupança"), ("PAGAMENTO", "Conta de pagamento"), ("OUTRA", "Outra")], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="chave_pix",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="favorecido",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="documento_favorecido",
            field=models.CharField(blank=True, max_length=14, null=True),
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="observacao_bancaria",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="fornecedor",
            name="cnpj",
            field=models.CharField(blank=True, db_index=True, max_length=18, null=True, validators=[cadastros.validators.cnpj_validator]),
        ),
        migrations.CreateModel(
            name="FornecedorCategoria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("categoria", models.CharField(choices=[("MATERIA_PRIMA", "Matéria-prima"), ("AVIAMENTO", "Aviamento"), ("REVENDA", "Produto de revenda"), ("FACCAO", "Facção"), ("PRESTADOR", "Prestador de serviço"), ("TRANSPORTADORA", "Transportadora"), ("OUTROS", "Outros")], db_index=True, max_length=20)),
                ("data_cadastro", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="fornecedor_categorias", to="cadastros.empresa")),
                ("fornecedor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="categorias_rel", to="cadastros.fornecedor")),
            ],
        ),
        migrations.CreateModel(
            name="FornecedorContato",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=80)),
                ("cargo_funcao", models.CharField(blank=True, max_length=80, null=True)),
                ("tipo", models.CharField(choices=[("COMERCIAL", "Comercial"), ("FINANCEIRO", "Financeiro"), ("FISCAL", "Fiscal"), ("PRODUCAO_FACCAO", "Produção/Facção"), ("LOGISTICA", "Logística"), ("OUTRO", "Outro")], db_index=True, default="COMERCIAL", max_length=20)),
                ("telefone", models.CharField(blank=True, max_length=15, null=True, validators=[cadastros.validators.telefone_br_validator])),
                ("whatsapp", models.CharField(blank=True, max_length=15, null=True, validators=[cadastros.validators.telefone_br_validator])),
                ("email", models.CharField(blank=True, max_length=80, null=True, validators=[cadastros.validators.email_simple_validator])),
                ("observacao", models.TextField(blank=True, null=True)),
                ("principal", models.BooleanField(db_index=True, default=False)),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("data_cadastro", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("data_atualizacao", models.DateTimeField(auto_now=True)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="fornecedor_contatos", to="cadastros.empresa")),
                ("fornecedor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contatos", to="cadastros.fornecedor")),
            ],
        ),
        migrations.CreateModel(
            name="FornecedorEndereco",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("FISCAL", "Fiscal"), ("COMERCIAL", "Comercial"), ("COBRANCA", "Cobrança"), ("RETIRADA_COLETA", "Retirada/Coleta"), ("ENTREGA", "Entrega"), ("UNIDADE_FABRIL", "Unidade fabril"), ("OUTRO", "Outro")], db_index=True, default="FISCAL", max_length=20)),
                ("logradouro", models.CharField(blank=True, max_length=50, null=True)),
                ("endereco", models.CharField(max_length=80)),
                ("numero", models.CharField(blank=True, max_length=10, null=True)),
                ("complemento", models.CharField(blank=True, max_length=100, null=True)),
                ("cep", models.CharField(blank=True, max_length=10, null=True, validators=[cadastros.validators.cep_validator])),
                ("bairro", models.CharField(blank=True, max_length=40, null=True)),
                ("cidade", models.CharField(blank=True, db_index=True, max_length=50, null=True)),
                ("estado", models.CharField(blank=True, db_index=True, max_length=2, null=True)),
                ("principal", models.BooleanField(db_index=True, default=False)),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("observacao", models.TextField(blank=True, null=True)),
                ("data_cadastro", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("data_atualizacao", models.DateTimeField(auto_now=True)),
                ("empresa", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="fornecedor_enderecos", to="cadastros.empresa")),
                ("fornecedor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enderecos", to="cadastros.fornecedor")),
            ],
        ),
        migrations.RunPython(migrar_fornecedores, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="fornecedor",
            name="empresa",
            field=models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="fornecedores", to="cadastros.empresa"),
        ),
        migrations.AddConstraint(
            model_name="fornecedor",
            constraint=models.UniqueConstraint(fields=("empresa", "documento"), name="uq_empresa_fornecedor_documento"),
        ),
        migrations.AddConstraint(
            model_name="fornecedorcategoria",
            constraint=models.UniqueConstraint(fields=("fornecedor", "categoria"), name="uq_fornecedor_categoria"),
        ),
        migrations.AddIndex(model_name="fornecedor", index=models.Index(fields=["empresa", "documento"], name="idx_forn_empresa_doc")),
        migrations.AddIndex(model_name="fornecedor", index=models.Index(fields=["empresa", "nome_fornecedor"], name="idx_forn_empresa_nome")),
        migrations.AddIndex(model_name="fornecedor", index=models.Index(fields=["empresa", "ativo"], name="idx_forn_empresa_ativo")),
        migrations.AddIndex(model_name="fornecedor", index=models.Index(fields=["empresa", "bloqueio"], name="idx_forn_empresa_bloq")),
        migrations.AddIndex(model_name="fornecedor", index=models.Index(fields=["empresa", "tipo_pessoa"], name="idx_forn_empresa_tipo")),
        migrations.AddIndex(model_name="fornecedorcategoria", index=models.Index(fields=["empresa", "categoria"], name="idx_forncat_empresa_cat")),
        migrations.AddIndex(model_name="fornecedorcontato", index=models.Index(fields=["empresa", "fornecedor", "tipo"], name="idx_forncont_emp_forn_tipo")),
        migrations.AddIndex(model_name="fornecedorcontato", index=models.Index(fields=["empresa", "ativo"], name="idx_forncont_emp_ativo")),
        migrations.AddIndex(model_name="fornecedorendereco", index=models.Index(fields=["empresa", "fornecedor", "tipo"], name="idx_fornend_emp_forn_tipo")),
        migrations.AddIndex(model_name="fornecedorendereco", index=models.Index(fields=["empresa", "ativo"], name="idx_fornend_emp_ativo")),
    ]
