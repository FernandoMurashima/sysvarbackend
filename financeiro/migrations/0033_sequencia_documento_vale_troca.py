import re

from django.db import migrations, models
import django.db.models.deletion


TIPO_VALE_TROCA = "VALE_TROCA"
LIMITE_VALE_TROCA = 9999999
PADRAO_VALE_TROCA = re.compile(r"^VT[0-9]{7}$")


def _documento(numero):
    return f"VT{numero:07d}"


def migrar_vales_troca(apps, schema_editor):
    ValeTroca = apps.get_model("financeiro", "ValeTroca")
    SequenciaDocumento = apps.get_model("financeiro", "SequenciaDocumento")
    Empresa = apps.get_model("cadastros", "Empresa")

    empresas_ids = set(ValeTroca.objects.exclude(empresa_id__isnull=True).values_list("empresa_id", flat=True))
    for empresa_id in sorted(empresas_ids):
        usados = set()
        proximo = 1
        vales = list(
            ValeTroca.objects.filter(empresa_id=empresa_id)
            .order_by("criado_em", "Idvaletroca")
            .values("Idvaletroca", "documento", "valor_original", "saldo", "status")
        )
        for vale in vales:
            atual = str(vale["documento"] or "").upper()
            if PADRAO_VALE_TROCA.fullmatch(atual) and atual not in usados:
                novo = atual
                numero = int(novo[2:])
                proximo = max(proximo, numero + 1)
            else:
                while _documento(proximo) in usados:
                    proximo += 1
                if proximo > LIMITE_VALE_TROCA:
                    raise RuntimeError("Faixa de numeracao de Vale-Troca esgotada durante migration.")
                novo = _documento(proximo)
                proximo += 1
            usados.add(novo)
            updates = {"documento": novo}
            if novo != vale["documento"]:
                updates["documento_legado"] = vale["documento"]
            ValeTroca.objects.filter(pk=vale["Idvaletroca"]).update(**updates)
        empresa = Empresa.objects.get(pk=empresa_id)
        SequenciaDocumento.objects.update_or_create(
            empresa=empresa,
            tipo_documento=TIPO_VALE_TROCA,
            defaults={"proximo_numero": proximo},
        )


def reverter_vales_troca(apps, schema_editor):
    ValeTroca = apps.get_model("financeiro", "ValeTroca")
    for vale in ValeTroca.objects.exclude(documento_legado__isnull=True).exclude(documento_legado=""):
        vale.documento = vale.documento_legado
        vale.documento_legado = None
        vale.save(update_fields=["documento", "documento_legado"])


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0030_loja_codigo_municipio_ibge"),
        ("financeiro", "0032_vale_troca_reserva"),
    ]

    operations = [
        migrations.CreateModel(
            name="SequenciaDocumento",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tipo_documento", models.CharField(choices=[("VALE_TROCA", "Vale-Troca")], max_length=40)),
                ("proximo_numero", models.PositiveIntegerField(default=1)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sequencias_documento", to="cadastros.empresa")),
            ],
            options={
                "db_table": "financeiro_sequencia_documento",
            },
        ),
        migrations.AddField(
            model_name="valetroca",
            name="documento_legado",
            field=models.CharField(blank=True, db_index=True, max_length=80, null=True),
        ),
        migrations.AlterField(
            model_name="valetroca",
            name="documento",
            field=models.CharField(db_index=True, max_length=50),
        ),
        migrations.AddConstraint(
            model_name="sequenciadocumento",
            constraint=models.UniqueConstraint(fields=("empresa", "tipo_documento"), name="uq_seq_doc_empresa_tipo"),
        ),
        migrations.RunPython(migrar_vales_troca, reverter_vales_troca),
        migrations.AddConstraint(
            model_name="valetroca",
            constraint=models.UniqueConstraint(fields=("empresa", "documento"), name="uq_vale_troca_empresa_doc"),
        ),
    ]
