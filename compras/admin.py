from django.contrib import admin
from .models import Cotacao, CotacaoFornecedor, CotacaoItem, CotacaoProposta, CotacaoPropostaItem, CotacaoRequisicao, PedidoCompra, PedidoCompraItem, PedidoCompraEntrega, PedidoCompraParcela

class PedidoCompraParcelaInline(admin.TabularInline):
    model = PedidoCompraParcela
    extra = 0
    fields = ("parcela_n", "vencimento", "valor", "percentual", "origem", "status", "pagar_item_id", "data_cadastro")
    readonly_fields = ("data_cadastro", "pagar_item_id")

class PedidoCompraEntregaInline(admin.TabularInline):
    model = PedidoCompraEntrega
    extra = 0
    fields = ("qtd_prevista", "data_prevista", "qtd_recebida", "data_recebida", "status", "observacao")
    readonly_fields = ()
    show_change_link = True

class PedidoCompraItemInline(admin.TabularInline):
    model = PedidoCompraItem
    extra = 0
    fields = (
        "produto", "cor", "pack", "n_packs",
        "descricao_livre",
        "qtd", "preco_unit", "desconto_valor", "total_item",
        "observacoes",
    )
    readonly_fields = ("total_item",)
    show_change_link = True


class CotacaoRequisicaoInline(admin.TabularInline):
    model = CotacaoRequisicao
    extra = 0
    fields = ("requisicao", "criado_em")
    readonly_fields = ("criado_em",)


class CotacaoItemInline(admin.TabularInline):
    model = CotacaoItem
    extra = 0
    fields = ("produto", "descricao", "quantidade_cotar", "unidade", "origem", "requisicao_item_origem", "permite_alternativo")
    show_change_link = True


class CotacaoFornecedorInline(admin.TabularInline):
    model = CotacaoFornecedor
    extra = 0
    fields = ("fornecedor", "status_participacao", "motivo_desclassificacao", "observacao", "criado_em")
    readonly_fields = ("criado_em",)
    show_change_link = True


@admin.register(Cotacao)
class CotacaoAdmin(admin.ModelAdmin):
    list_display = ("id", "numero", "empresa", "loja", "responsavel", "data_abertura", "status", "prioridade", "tipo_compra")
    list_filter = ("status", "prioridade", "tipo_compra", "empresa", "loja", "data_abertura")
    search_fields = ("numero", "observacao", "responsavel__username")
    readonly_fields = ("numero", "criado_em", "atualizado_em")
    inlines = [CotacaoRequisicaoInline, CotacaoItemInline, CotacaoFornecedorInline]


@admin.register(CotacaoItem)
class CotacaoItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cotacao", "produto", "descricao", "quantidade_cotar", "unidade", "origem")
    list_filter = ("origem", "cotacao__empresa", "cotacao__status")
    search_fields = ("id", "cotacao__numero", "produto__descricao", "descricao")


@admin.register(CotacaoRequisicao)
class CotacaoRequisicaoAdmin(admin.ModelAdmin):
    list_display = ("id", "cotacao", "requisicao", "criado_em")
    list_filter = ("cotacao__empresa", "cotacao__status")
    search_fields = ("cotacao__numero", "requisicao__numero")


@admin.register(CotacaoFornecedor)
class CotacaoFornecedorAdmin(admin.ModelAdmin):
    list_display = ("id", "cotacao", "fornecedor", "status_participacao", "criado_em")
    list_filter = ("status_participacao", "cotacao__empresa", "cotacao__status")
    search_fields = ("cotacao__numero", "fornecedor__nome_fornecedor")


class CotacaoPropostaItemInline(admin.TabularInline):
    model = CotacaoPropostaItem
    extra = 0
    fields = ("cotacao_item", "quantidade_ofertada", "preco_unitario", "desconto_item", "marca", "modelo_referencia", "total_item")
    readonly_fields = ("total_item",)


@admin.register(CotacaoProposta)
class CotacaoPropostaAdmin(admin.ModelAdmin):
    list_display = ("id", "cotacao", "cotacao_fornecedor", "data_proposta", "total_proposta", "ativa")
    list_filter = ("ativa", "cotacao__empresa", "cotacao__status", "data_proposta")
    search_fields = ("cotacao__numero", "cotacao_fornecedor__fornecedor__nome_fornecedor")
    readonly_fields = ("total_itens", "total_proposta", "criado_em", "atualizado_em")
    inlines = [CotacaoPropostaItemInline]


@admin.register(CotacaoPropostaItem)
class CotacaoPropostaItemAdmin(admin.ModelAdmin):
    list_display = ("id", "proposta", "cotacao_item", "quantidade_ofertada", "preco_unitario", "desconto_item", "total_item")
    list_filter = ("proposta__cotacao__empresa",)
    search_fields = ("proposta__cotacao__numero", "cotacao_item__descricao")

@admin.register(PedidoCompra)
class PedidoCompraAdmin(admin.ModelAdmin):
    list_display = ("id", "tipo", "loja", "fornecedor", "emissao", "status", "forma_pagamento", "prazo_pagamento", "total_pedido")
    list_filter = ("tipo", "status", "loja", "fornecedor", "emissao", "forma_pagamento", "prazo_pagamento")
    search_fields = ("id", "fornecedor__RazaoSocial", "fornecedor__NomeFantasia")
    date_hierarchy = "emissao"
    readonly_fields = ("total_itens", "total_desconto", "total_pedido", "data_cadastro")
    inlines = [PedidoCompraItemInline, PedidoCompraParcelaInline]
    fieldsets = (
        ("Identificação", {"fields": ("tipo", "loja", "fornecedor", "emissao", "previsao_entrega", "status")}),
        ("Pagamento", {"fields": ("forma_pagamento", "prazo_pagamento")}),
        ("Totais", {"fields": ("total_itens", "total_desconto", "frete", "total_pedido")}),
        ("Outros", {"fields": ("observacoes", "data_cadastro")}),
    )

@admin.register(PedidoCompraItem)
class PedidoCompraItemAdmin(admin.ModelAdmin):
    list_display = ("id", "pedido", "produto", "cor", "pack", "n_packs", "qtd", "preco_unit", "total_item")
    list_filter = ("pedido__loja", "pedido__fornecedor", "pedido__tipo")
    search_fields = ("id", "pedido__id", "produto__descricao", "produto__referencia")
    readonly_fields = ("total_item",)
    inlines = [PedidoCompraEntregaInline]
    fieldsets = (
        ("Vínculo", {"fields": ("pedido",)}),
        ("Revenda", {"fields": ("produto", "cor", "pack", "n_packs")}),
        ("Uso/Consumo", {"fields": ("descricao_livre",)}),
        ("Valores", {"fields": ("qtd", "preco_unit", "desconto_valor", "total_item")}),
        ("Outros", {"fields": ("observacoes",)}),
    )

@admin.register(PedidoCompraEntrega)
class PedidoCompraEntregaAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "qtd_prevista", "data_prevista", "qtd_recebida", "data_recebida", "status")
    list_filter = ("status", "data_prevista", "data_recebida")
    search_fields = ("id", "item__pedido__id", "item__produto__descricao", "item__produto__referencia")

@admin.register(PedidoCompraParcela)
class PedidoCompraParcelaAdmin(admin.ModelAdmin):
    list_display = ("id", "pedido", "parcela_n", "vencimento", "valor", "percentual", "origem", "status", "pagar_item_id")
    list_filter = ("status", "origem", "vencimento")
    search_fields = ("pedido__id",)
    readonly_fields = ("data_cadastro",)
