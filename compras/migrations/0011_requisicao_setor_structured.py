from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


DEFAULT_SETORES = [
    "Administrativo",
    "Financeiro",
    "TI",
    "Almoxarifado",
    "Estoque",
    "Vendas",
    "Diretoria",
    "Manutencao",
]


def seed_setores(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Setor = apps.get_model("compras", "RequisicaoSetor")
    for empresa in Empresa.objects.all().only("pk"):
        for nome in DEFAULT_SETORES:
            Setor.objects.get_or_create(
                empresa_id=empresa.pk,
                nome=nome,
                defaults={"controla_estoque_uso_consumo": nome in {"Almoxarifado", "Estoque"}},
            )


def migrate_setor_text_to_fk(apps, schema_editor):
    Requisicao = apps.get_model("compras", "Requisicao")
    Setor = apps.get_model("compras", "RequisicaoSetor")
    for requisicao in Requisicao.objects.all().only("pk", "empresa_id", "setor"):
        nome = (requisicao.setor or "Administrativo").strip() or "Administrativo"
        setor, _ = Setor.objects.get_or_create(empresa_id=requisicao.empresa_id, nome=nome)
        requisicao.setor_ref_id = setor.pk
        requisicao.save(update_fields=["setor_ref"])


def migrate_setor_fk_to_text(apps, schema_editor):
    Requisicao = apps.get_model("compras", "Requisicao")
    for requisicao in Requisicao.objects.select_related("setor").all():
        requisicao.setor = requisicao.setor.nome if requisicao.setor_id else ""
        requisicao.save(update_fields=["setor"])


class Migration(migrations.Migration):

    dependencies = [
        ("compras", "0010_seed_requisicao_servico_categorias"),
    ]

    operations = [
        migrations.CreateModel(
            name="RequisicaoSetor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=80)),
                ("descricao", models.TextField(blank=True, default="")),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("pode_fazer_requisicao", models.BooleanField(default=True)),
                ("recebe_requisicoes", models.BooleanField(default=True)),
                ("controla_estoque_uso_consumo", models.BooleanField(default=False)),
                ("data_cadastro", models.DateTimeField(default=django.utils.timezone.now)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="setores_requisicao", to="cadastros.empresa")),
            ],
            options={
                "db_table": "compras_requisicao_setor",
                "ordering": ["nome"],
            },
        ),
        migrations.AddConstraint(
            model_name="requisicaosetor",
            constraint=models.UniqueConstraint(fields=("empresa", "nome"), name="uq_req_setor_empresa_nome"),
        ),
        migrations.AddIndex(
            model_name="requisicaosetor",
            index=models.Index(fields=["empresa", "ativo"], name="compras_req_empresa_d4b6ad_idx"),
        ),
        migrations.RunPython(seed_setores, migrations.RunPython.noop),
        migrations.AddField(
            model_name="requisicao",
            name="setor_ref",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="requisicoes", to="compras.requisicaosetor"),
        ),
        migrations.RunPython(migrate_setor_text_to_fk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="requisicao",
            name="setor",
        ),
        migrations.RenameField(
            model_name="requisicao",
            old_name="setor_ref",
            new_name="setor",
        ),
        migrations.AlterField(
            model_name="requisicao",
            name="setor",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requisicoes", to="compras.requisicaosetor"),
        ),
    ]
