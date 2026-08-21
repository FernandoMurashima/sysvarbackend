from django.db import migrations, models
import django.db.models.deletion


def copy_sensitive_permissions_to_profiles(apps, schema_editor):
    PerfilProcessPermission = apps.get_model("accounts", "PerfilProcessPermission")
    UserFieldPermission = apps.get_model("accounts", "UserFieldPermission")
    for perm in UserFieldPermission.objects.filter(pode_ver=True).select_related("user__perfil_principal"):
        perfil = getattr(perm.user, "perfil_principal", None)
        if perfil:
            PerfilProcessPermission.objects.update_or_create(
                perfil=perfil,
                codigo=perm.campo,
                defaults={"permitido": True},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_clean_requisicoes_copied_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="perfilmodulopermissao",
            name="pode_excluir",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.CreateModel(
            name="PerfilProcessPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(choices=[("requisicoes.fazer", "Requisições - fazer"), ("requisicoes.aprovar", "Requisições - aprovar"), ("requisicoes.atender", "Requisições - atender"), ("pedido_compra.aprovar", "Pedido de compra - aprovar"), ("vendas.autorizar_desconto", "Vendas - autorizar desconto"), ("funcionario.salario", "Funcionário - salário"), ("produto.custo", "Produto - custos e margens")], db_index=True, max_length=80)),
                ("permitido", models.BooleanField(db_index=True, default=False)),
                ("perfil", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="permissoes_processos", to="accounts.perfilacesso")),
            ],
            options={
                "ordering": ["perfil_id", "codigo"],
                "constraints": [models.UniqueConstraint(fields=("perfil", "codigo"), name="uq_perfil_process_permission")],
            },
        ),
        migrations.RunPython(copy_sensitive_permissions_to_profiles, migrations.RunPython.noop),
    ]
