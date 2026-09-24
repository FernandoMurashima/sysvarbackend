from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def _codigo_adquirente_livre(Adquirente, empresa_id, descricao):
    base = "".join(ch for ch in str(descricao or "ADQ").upper() if ch.isalnum())[:20] or "ADQ"
    if not Adquirente.objects.filter(empresa_id=empresa_id, codigo=base).exists():
        return base
    for seq in range(1, 1000):
        sufixo = str(seq)
        candidato = f"{base[:20 - len(sufixo)]}{sufixo}"
        if not Adquirente.objects.filter(empresa_id=empresa_id, codigo=candidato).exists():
            return candidato
    raise RuntimeError(f"Nao foi possivel gerar codigo de adquirente para {descricao}")


def preservar_e_migrar_taxas(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    Adquirente = apps.get_model("financeiro", "Adquirente")
    CondicaoAdquirente = apps.get_model("financeiro", "CondicaoAdquirente")

    q = schema_editor.quote_name
    schema_editor.execute(
        """
        CREATE TABLE IF NOT EXISTS {legado} AS
        SELECT
            {pk} AS forma_pagamento_id,
            {empresa_id} AS empresa_id,
            {codigo} AS forma_codigo,
            {descricao} AS forma_descricao,
            {prazo_id} AS prazo_pagamento_id,
            {adquirente} AS adquirente,
            {taxa_percentual} AS taxa_percentual,
            {taxa_fixa} AS taxa_fixa,
            CURRENT_TIMESTAMP AS preservado_em
        FROM {formas}
        WHERE COALESCE({adquirente}, '') <> ''
           OR COALESCE({taxa_percentual}, 0) <> 0
           OR COALESCE({taxa_fixa}, 0) <> 0
        """.format(
            legado=q("financeiro_forma_pagamento_taxa_legado"),
            formas=q("financeiro_forma_pagamento"),
            pk=q("Idformapagamento"),
            empresa_id=q("empresa_id"),
            codigo=q("codigo"),
            descricao=q("descricao"),
            prazo_id=q("prazo_pagamento_id"),
            adquirente=q("adquirente"),
            taxa_percentual=q("taxa_percentual"),
            taxa_fixa=q("taxa_fixa"),
        )
    )

    for forma in FormaPagamento.objects.exclude(prazo_pagamento_id__isnull=True):
        adquirente_nome = str(getattr(forma, "adquirente", "") or "").strip()
        taxa_percentual = Decimal(str(getattr(forma, "taxa_percentual", 0) or 0))
        taxa_fixa = Decimal(str(getattr(forma, "taxa_fixa", 0) or 0))
        if not adquirente_nome and taxa_percentual == 0 and taxa_fixa == 0:
            continue
        descricao = adquirente_nome or f"Adquirente migrada {forma.codigo}"
        adquirente = Adquirente.objects.filter(empresa_id=forma.empresa_id, descricao=descricao).first()
        if not adquirente:
            adquirente = Adquirente.objects.create(
                empresa_id=forma.empresa_id,
                codigo=_codigo_adquirente_livre(Adquirente, forma.empresa_id, descricao),
                descricao=descricao,
                ativo=True,
            )
        CondicaoAdquirente.objects.update_or_create(
            empresa_id=forma.empresa_id,
            adquirente=adquirente,
            forma_pagamento=forma,
            prazo_pagamento_id=forma.prazo_pagamento_id,
            defaults={
                "taxa_percentual": taxa_percentual,
                "taxa_fixa": taxa_fixa,
                "ativo": True,
            },
        )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("financeiro", "0029_remove_formapagamento_num_parcelas"),
    ]

    operations = [
        migrations.CreateModel(
            name="Adquirente",
            fields=[
                ("Idadquirente", models.BigAutoField(primary_key=True, serialize=False)),
                ("codigo", models.CharField(max_length=20)),
                ("descricao", models.CharField(max_length=120)),
                ("ativo", models.BooleanField(default=True)),
                ("data_cadastro", models.DateTimeField(default=django.utils.timezone.now)),
                ("empresa", models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="adquirentes", to="cadastros.empresa")),
            ],
            options={"db_table": "financeiro_adquirente", "ordering": ["codigo"]},
        ),
        migrations.CreateModel(
            name="CondicaoAdquirente",
            fields=[
                ("Idcondicaoadquirente", models.BigAutoField(primary_key=True, serialize=False)),
                ("taxa_percentual", models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ("taxa_fixa", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("ativo", models.BooleanField(default=True)),
                ("data_cadastro", models.DateTimeField(default=django.utils.timezone.now)),
                ("adquirente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="condicoes", to="financeiro.adquirente")),
                ("empresa", models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="condicoes_adquirente", to="cadastros.empresa")),
                ("forma_pagamento", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="condicoes_adquirente", to="financeiro.formapagamento")),
                ("prazo_pagamento", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="condicoes_adquirente", to="financeiro.prazopagamento")),
            ],
            options={"db_table": "financeiro_condicao_adquirente", "ordering": ["adquirente__codigo", "forma_pagamento__codigo", "prazo_pagamento__codigo"]},
        ),
        migrations.AddConstraint(
            model_name="adquirente",
            constraint=models.UniqueConstraint(fields=("empresa", "codigo"), name="uq_empresa_adquirente_codigo"),
        ),
        migrations.AddConstraint(
            model_name="condicaoadquirente",
            constraint=models.UniqueConstraint(fields=("empresa", "adquirente", "forma_pagamento", "prazo_pagamento"), name="uq_empresa_adq_forma_prazo"),
        ),
        migrations.AddIndex(
            model_name="condicaoadquirente",
            index=models.Index(fields=["empresa", "ativo"], name="financeiro__empresa_f3ebba_idx"),
        ),
        migrations.AddIndex(
            model_name="condicaoadquirente",
            index=models.Index(fields=["forma_pagamento", "prazo_pagamento"], name="financeiro__forma_p_6c9cbd_idx"),
        ),
        migrations.RunPython(preservar_e_migrar_taxas, migrations.RunPython.noop),
        migrations.RemoveField(model_name="formapagamento", name="adquirente"),
        migrations.RemoveField(model_name="formapagamento", name="taxa_percentual"),
        migrations.RemoveField(model_name="formapagamento", name="taxa_fixa"),
    ]
