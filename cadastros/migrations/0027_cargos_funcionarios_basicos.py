from django.db import migrations


CARGOS = [
    ("VENDEDOR", "Vendedor", True, True, True, False, False),
    ("CAIXA", "Caixa", False, False, True, False, False),
    ("GERENTE", "Gerente", False, False, True, False, True),
    ("SUPERVISOR", "Supervisor", False, False, True, True, True),
    ("ASSISTENTE", "Assistente", False, False, False, False, False),
    ("AUXILIAR", "Auxiliar", False, False, False, False, False),
    ("AUXADM", "Auxiliar Administrativo", False, False, False, False, False),
    ("ASSADM", "Assistente Administrativo", False, False, False, False, False),
    ("ASSFIN", "Assistente Financeiro", False, False, False, False, False),
    ("AUXFIN", "Auxiliar Financeiro", False, False, False, False, False),
    ("COMPRADOR", "Comprador", False, False, False, False, False),
    ("ESTOQUISTA", "Estoquista", False, False, True, False, False),
    ("ALMOX", "Almoxarife", False, False, True, False, False),
    ("CONFERENTE", "Conferente", False, False, True, False, False),
    ("RECEBEDOR", "Recebedor", False, False, True, False, False),
    ("COSTUREIRA", "Costureira", False, False, False, False, False),
    ("AUXPROD", "Auxiliar de Produção", False, False, False, False, False),
]


def criar_cargos_basicos(apps, schema_editor):
    Empresa = apps.get_model("cadastros", "Empresa")
    Cargo = apps.get_model("cadastros", "Cargo")
    for empresa in Empresa.objects.all().iterator():
        for codigo, descricao, participa_vendas, permite_comissao, autoridade_loja, multi_loja, gerencial in CARGOS:
            Cargo.objects.get_or_create(
                empresa_id=empresa.pk,
                codigo=codigo,
                defaults={
                    "descricao": descricao,
                    "ativo": True,
                    "participa_vendas": participa_vendas,
                    "permite_comissao": permite_comissao,
                    "autoridade_operacional_loja": autoridade_loja,
                    "permite_multiplas_lojas": multi_loja,
                    "gerencial": gerencial,
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0026_cargo_funcionariohistorico_funcionarios_comissionado_and_more"),
    ]

    operations = [
        migrations.RunPython(criar_cargos_basicos, migrations.RunPython.noop),
    ]
