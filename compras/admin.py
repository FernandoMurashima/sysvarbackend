from django.contrib import admin
from .models import PedidoCompra, PedidoCompraItem, PedidoCompraEntrega, PedidoCompraParcela

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
