from django.db import migrations, models
import django.db.models.deletion


def preencher_identidade_nfe(apps, schema_editor):
    NotaFiscalEntrada = apps.get_model("fiscal", "NotaFiscalEntrada")
    inconsistentes = []
    qs = NotaFiscalEntrada.objects.select_related("pedido_compra").filter(pedido_compra__isnull=False)
    for nota in qs.iterator():
        pedido = nota.pedido_compra
        if not pedido.empresa_id or not pedido.loja_id or not pedido.fornecedor_id:
            inconsistentes.append(str(nota.pk))
            continue
        nota.empresa_id = pedido.empresa_id
        nota.loja_id = pedido.loja_id
        nota.fornecedor_id = pedido.fornecedor_id
        nota.save(update_fields=["empresa", "loja", "fornecedor"])
    if inconsistentes:
        exemplos = ", ".join(inconsistentes[:20])
        raise RuntimeError(
            "Existem notas fiscais de entrada cujo pedido não possui empresa, loja ou fornecedor. "
            f"Regularize antes de aplicar a migration. Exemplos: {exemplos}"
        )


def validar_identidade_preenchida(apps, schema_editor):
    NotaFiscalEntrada = apps.get_model("fiscal", "NotaFiscalEntrada")
    faltantes = NotaFiscalEntrada.objects.filter(
        models.Q(empresa__isnull=True) | models.Q(loja__isnull=True) | models.Q(fornecedor__isnull=True)
    )
    if faltantes.exists():
        exemplos = ", ".join(str(pk) for pk in faltantes.values_list("pk", flat=True)[:20])
        raise RuntimeError(
            "Existem notas fiscais de entrada sem empresa, loja ou fornecedor após o preenchimento. "
            f"Regularize antes de aplicar a migration. Exemplos: {exemplos}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0028_corrige_comissao_gerente_supervisor"),
        ("compras", "0028_cotacaoitem_ordem_servico_material_origem_and_more"),
        ("fiscal", "0016_nfe_entrada_identidade_chave"),
    ]

    operations = [
        migrations.AddField(
            model_name="notafiscalentrada",
            name="empresa",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_fiscais_entrada",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddField(
            model_name="notafiscalentrada",
            name="loja",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_fiscais_entrada",
                to="cadastros.loja",
            ),
        ),
        migrations.AddField(
            model_name="notafiscalentrada",
            name="fornecedor",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_fiscais_entrada",
                to="cadastros.fornecedor",
            ),
        ),
        migrations.RunPython(preencher_identidade_nfe, migrations.RunPython.noop),
        migrations.RunPython(validar_identidade_preenchida, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="notafiscalentrada",
            name="empresa",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_fiscais_entrada",
                to="cadastros.empresa",
            ),
        ),
        migrations.AlterField(
            model_name="notafiscalentrada",
            name="loja",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_fiscais_entrada",
                to="cadastros.loja",
            ),
        ),
        migrations.AlterField(
            model_name="notafiscalentrada",
            name="fornecedor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_fiscais_entrada",
                to="cadastros.fornecedor",
            ),
        ),
        migrations.AlterField(
            model_name="notafiscalentrada",
            name="pedido_compra",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="notas_entrada",
                to="compras.pedidocompra",
            ),
        ),
        migrations.AddIndex(
            model_name="notafiscalentrada",
            index=models.Index(fields=["empresa", "status"], name="ix_fiscal_nfe_empresa_status"),
        ),
        migrations.AddConstraint(
            model_name="notafiscalentrada",
            constraint=models.UniqueConstraint(
                fields=("empresa", "fornecedor", "modelo", "serie", "numero"),
                name="uq_fiscal_nfe_emp_forn_doc",
            ),
        ),
    ]
