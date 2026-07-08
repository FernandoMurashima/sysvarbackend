from django.contrib import admin
from .models import Empresa, Loja, Cliente, Fornecedor, Funcionarios, Nat_Lancamento, PlanoContabil


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("id", "nome", "nome_fantasia", "documento", "ativo", "data_cadastro")
    list_filter = ("ativo",)
    search_fields = ("nome", "nome_fantasia", "documento")
    ordering = ("nome",)
    readonly_fields = ("data_cadastro",)


@admin.register(Loja)
class LojaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "empresa",
        "nome_loja",
        "apelido_loja",
        "cnpj",
        "cidade",
        "estado",
        "telefone1",
        "EstoqueNegativo",
        "Rede",
        "Matriz",
        "DataAbertura",
        "DataEnceramento",
        "data_cadastro",
        "ativo",
    )
    list_filter = (
        "ativo",
        "empresa",
        "estado",
        "EstoqueNegativo",
        "Rede",
        "Matriz",
    )
    search_fields = (
        "nome_loja",
        "apelido_loja",
        "cnpj",
        "cidade",
        "email",
        "telefone1",
        "telefone2",
        "ContaContabil",
    )
    ordering = ("nome_loja",)
    readonly_fields = ("data_cadastro",)

    fieldsets = (
        ("Identificação", {
            "fields": ("nome_loja", "apelido_loja", "cnpj", "ativo")
        }),
        ("Empresa", {
            "fields": ("empresa",)
        }),
        ("Contato", {
            "fields": ("email", "telefone1", "telefone2")
        }),
        ("Endereço", {
            "fields": ("logradouro", "endereco", "numero", "complemento", "cep", "bairro", "cidade", "estado")
        }),
        ("Operacional", {
            "fields": ("EstoqueNegativo", "Rede", "Matriz", "ContaContabil", "DataAbertura", "DataEnceramento")
        }),
        ("Auditoria", {
            "fields": ("data_cadastro",)
        }),
    )


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "nome_cliente", "apelido", "cpf", "telefone1", "cidade", "bloqueio", "ativo", "data_cadastro")
    list_filter = ("empresa", "ativo", "estado", "categoria", "bloqueio")
    search_fields = ("nome_cliente", "apelido", "cpf", "email", "cidade")
    ordering = ("nome_cliente",)
    readonly_fields = ("data_cadastro",)


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "nome_fornecedor", "apelido", "cnpj", "telefone1", "cidade", "bloqueio", "ativo", "data_cadastro")
    list_filter = ("empresa", "ativo", "estado", "categoria", "bloqueio")
    search_fields = ("nome_fornecedor", "apelido", "cnpj", "email", "cidade")
    ordering = ("nome_fornecedor",)
    readonly_fields = ("data_cadastro",)


@admin.register(Funcionarios)
class FuncionariosAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "nomefuncionario", "apelido", "cpf", "categoria", "idloja", "meta", "comissao_percentual", "ativo", "data_cadastro")
    list_filter = ("empresa", "categoria", "ativo", "idloja")
    search_fields = ("nomefuncionario", "apelido", "cpf")
    ordering = ("nomefuncionario",)
    list_select_related = ("empresa", "idloja")
    readonly_fields = ("data_cadastro",)


@admin.register(Nat_Lancamento)
class NatLancamentoAdmin(admin.ModelAdmin):
    list_display = ("idnatureza", "empresa", "codigo", "categoria_principal", "subcategoria", "natureza_operacao", "tipo_natureza", "plano_contabil", "entra_dre", "movimenta_financeiro", "ativo")
    list_filter = ("empresa", "ativo", "natureza_operacao", "entra_dre", "movimenta_financeiro", "tipo_natureza", "categoria_principal", "subcategoria")
    search_fields = ("codigo", "descricao", "categoria_principal", "subcategoria", "categoria_gerencial", "conta_contabil", "plano_contabil__codigo", "plano_contabil__descricao")
    list_select_related = ("empresa", "plano_contabil")


@admin.register(PlanoContabil)
class PlanoContabilAdmin(admin.ModelAdmin):
    list_display = ("id", "empresa", "codigo", "descricao", "classe", "natureza", "conta_pai", "nivel", "analitica", "ativa")
    list_filter = ("empresa", "classe", "natureza", "analitica", "ativa")
    search_fields = ("codigo", "descricao", "conta_pai__codigo", "conta_pai__descricao")
    list_select_related = ("empresa", "conta_pai")
    ordering = ("empresa", "codigo")
