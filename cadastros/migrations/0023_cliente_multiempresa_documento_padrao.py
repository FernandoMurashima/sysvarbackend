from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def only_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def migrar_documento_cliente(apps, schema_editor):
    Cliente = apps.get_model("cadastros", "Cliente")
    sem_empresa = list(Cliente.objects.filter(empresa__isnull=True).values_list("id", flat=True)[:20])
    if sem_empresa:
        raise RuntimeError(
            "Existem clientes sem empresa. Execute diagnosticar_clientes_sem_empresa antes desta migration. "
            f"Exemplos: {sem_empresa}"
        )
    for cliente in Cliente.objects.all().iterator():
        doc = only_digits(getattr(cliente, "documento", None) or getattr(cliente, "cpf", None))
        update_fields = []
        if doc:
            cliente.documento = doc
            cliente.cpf = doc
            update_fields.extend(["documento", "cpf"])
            if doc == "00000000000":
                cliente.tipo_pessoa = "PF"
                cliente.cliente_padrao = True
                cliente.ativo = True
                cliente.bloqueio = False
                update_fields.extend(["tipo_pessoa", "cliente_padrao", "ativo", "bloqueio"])
            elif len(doc) == 14:
                cliente.tipo_pessoa = "PJ"
                update_fields.append("tipo_pessoa")
        if update_fields:
            cliente.save(update_fields=sorted(set(update_fields)))


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0022_loja_empresa_obrigatoria"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="tipo_pessoa",
            field=models.CharField(
                choices=[("PF", "Pessoa física"), ("PJ", "Pessoa jurídica")],
                db_index=True,
                default="PF",
                max_length=2,
            ),
        ),
        migrations.AddField(
            model_name="cliente",
            name="documento",
            field=models.CharField(blank=True, db_index=True, max_length=14, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="cliente_padrao",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="cliente",
            name="motivo_bloqueio",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="observacao_bloqueio",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="bloqueado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="bloqueado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="clientes_bloqueados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="cliente",
            name="aceita_email",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="cliente",
            name="aceita_whatsapp",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="cliente",
            name="aceita_sms",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="cliente",
            name="consentimento_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="origem_consentimento",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="consentimento_observacao",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(migrar_documento_cliente, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="cliente",
            name="cpf",
            field=models.CharField(blank=True, db_index=True, max_length=15, null=True),
        ),
        migrations.AlterField(
            model_name="cliente",
            name="empresa",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clientes",
                to="cadastros.empresa",
            ),
        ),
        migrations.AddConstraint(
            model_name="cliente",
            constraint=models.UniqueConstraint(fields=("empresa", "documento"), name="uq_empresa_cliente_documento"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "documento"], name="idx_cliente_empresa_doc"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "nome_cliente"], name="idx_cliente_empresa_nome"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "ativo"], name="idx_cliente_empresa_ativo"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "bloqueio"], name="idx_cliente_empresa_bloq"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "tipo_pessoa"], name="idx_cliente_empresa_tipo"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "cliente_padrao"], name="idx_cliente_empresa_padrao"),
        ),
        migrations.AddIndex(
            model_name="cliente",
            index=models.Index(fields=["empresa", "cidade", "estado"], name="idx_cliente_empresa_cid_uf"),
        ),
    ]
