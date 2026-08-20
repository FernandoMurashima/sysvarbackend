from django.db import migrations, models


MODULES = [
    ("requisicoes", "Requisições", "Acesso e criação das próprias requisições.", 85),
    ("requisicoes_analise", "Requisições - análise", "Análise, aprovação, devolução e rejeição de requisições.", 86),
    ("requisicoes_atendimento", "Requisições - atendimento", "Atendimento operacional de requisições aprovadas.", 87),
    ("requisicoes_todas", "Requisições - todas", "Consulta gerencial de todas as requisições no escopo.", 88),
]


def create_requisicoes_modules(apps, schema_editor):
    ModuloSistema = apps.get_model("cadastros", "ModuloSistema")
    EmpresaModulo = apps.get_model("cadastros", "EmpresaModulo")
    UserModulePermission = apps.get_model("accounts", "UserModulePermission")
    PerfilModuloPermissao = apps.get_model("accounts", "PerfilModuloPermissao")
    EmpresaContrato = apps.get_model("cadastros", "EmpresaContrato")

    created = {}
    for chave, nome, descricao, ordem in MODULES:
        modulo, _ = ModuloSistema.objects.update_or_create(
            chave=chave,
            defaults={
                "nome": nome,
                "descricao": descricao,
                "categoria": "COMERCIAL",
                "basico": False,
                "ativo": True,
                "ordem": ordem,
                "dependencias": [],
            },
        )
        created[chave] = modulo

    compras_modulo = ModuloSistema.objects.filter(chave="compras").first()
    if not compras_modulo:
        return

    empresas_com_compras = set(
        EmpresaModulo.objects.filter(modulo=compras_modulo, contratado=True).values_list("empresa_id", flat=True)
    )
    empresas_com_compras.update(
        EmpresaContrato.objects.filter(plano_completo=True).values_list("empresa_id", flat=True)
    )
    for empresa_id in empresas_com_compras:
        for modulo in created.values():
            EmpresaModulo.objects.update_or_create(
                empresa_id=empresa_id,
                modulo=modulo,
                defaults={"contratado": True},
            )

    for perm in UserModulePermission.objects.filter(modulo="compras").exclude(acesso="NONE"):
        for chave in created:
            UserModulePermission.objects.update_or_create(
                user_id=perm.user_id,
                modulo=chave,
                defaults={"acesso": perm.acesso},
            )

    for perfil_perm in PerfilModuloPermissao.objects.filter(modulo=compras_modulo).exclude(acesso="NONE"):
        for modulo in created.values():
            PerfilModuloPermissao.objects.update_or_create(
                perfil_id=perfil_perm.perfil_id,
                modulo=modulo,
                defaults={"acesso": perfil_perm.acesso},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0028_corrige_comissao_gerente_supervisor"),
        ("accounts", "0010_user_deve_trocar_senha"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usermodulepermission",
            name="modulo",
            field=models.CharField(
                choices=[
                    ("operacional", "Operacional"),
                    ("cadastros", "Cadastros"),
                    ("produtos", "Produtos"),
                    ("fiscal", "Fiscal"),
                    ("fiscal_contabil", "Fiscal e Contábil"),
                    ("estoque", "Estoque"),
                    ("distribuicao", "Distribuição"),
                    ("vendas", "Vendas"),
                    ("compras", "Compras"),
                    ("requisicoes", "Requisições"),
                    ("requisicoes_analise", "Requisições - análise"),
                    ("requisicoes_atendimento", "Requisições - atendimento"),
                    ("requisicoes_todas", "Requisições - todas"),
                    ("producao", "Produção"),
                    ("financeiro", "Financeiro"),
                    ("relatorios", "Relatórios"),
                    ("configuracoes", "Configurações"),
                    ("auditoria", "Auditoria"),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
        migrations.RunPython(create_requisicoes_modules, migrations.RunPython.noop),
    ]
