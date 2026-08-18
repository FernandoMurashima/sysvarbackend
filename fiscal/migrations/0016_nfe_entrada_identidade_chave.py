from django.db import migrations, models


def preparar_chaves(apps, schema_editor):
    NotaFiscalEntrada = apps.get_model("fiscal", "NotaFiscalEntrada")
    duplicadas = (
        NotaFiscalEntrada.objects.exclude(chave_acesso__isnull=True)
        .exclude(chave_acesso="")
        .values("chave_acesso")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    if duplicadas.exists():
        chaves = ", ".join(row["chave_acesso"] for row in duplicadas[:10])
        raise RuntimeError(
            "Existem chaves de acesso duplicadas em NotaFiscalEntrada. "
            f"Regularize antes de aplicar a migration. Exemplos: {chaves}"
        )
    NotaFiscalEntrada.objects.filter(chave_acesso="").update(chave_acesso=None)


def remover_constraint_documento_antiga(apps, schema_editor):
    table = "fiscal_nota_fiscal_entrada"
    name = "uq_fiscal_nfe_pedido_modelo_serie_numero"
    constraints = schema_editor.connection.introspection.get_constraints(schema_editor.connection.cursor(), table)
    if name in constraints:
        schema_editor.execute(f"ALTER TABLE `{table}` DROP INDEX `{name}`")


class Migration(migrations.Migration):
    dependencies = [
        ("fiscal", "0015_notafiscalsaida_autorizada_em_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notafiscalentrada",
            name="chave_acesso",
            field=models.CharField(blank=True, default=None, max_length=60, null=True),
        ),
        migrations.RunPython(preparar_chaves, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(remover_constraint_documento_antiga, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="notafiscalentrada",
                    name="uq_fiscal_nfe_pedido_modelo_serie_numero",
                ),
            ],
        ),
        migrations.AlterField(
            model_name="notafiscalentrada",
            name="chave_acesso",
            field=models.CharField(blank=True, db_index=True, default=None, max_length=44, null=True, unique=True),
        ),
    ]
